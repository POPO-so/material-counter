#include "jpeg_axis_gray.h"

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

using axis_word = ap_axiu<32, 0, 0, 0>;
struct beat_t { uint32_t data; uint32_t keep; bool last; };
struct result_t { unsigned status; bool input_empty; std::vector<beat_t> beats; };

static void require(bool ok, const std::string& message) {
    if (!ok) throw std::runtime_error(message);
}

static std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream f(path.c_str(), std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}

static std::string frame_stem(unsigned frame_id) {
    std::string digits = std::to_string(frame_id);
    while (digits.size() < 6) digits.insert(digits.begin(), '0');
    return "frame_" + digits;
}

static result_t invoke_on(hls::stream<axis_word>& in, hls::stream<axis_word>& out,
                          const std::vector<uint8_t>& jpeg, int advertised_size,
                          int bad_keep_beat = -1, int early_last_beat = -1,
                          unsigned frame_id = 1) {
    for (size_t i = 0, beat = 0; i < jpeg.size(); i += 4, ++beat) {
        axis_word w = {};
        ap_uint<4> keep = 0;
        for (unsigned lane = 0; lane < 4 && i + lane < jpeg.size(); ++lane) {
            w.data.range(lane * 8 + 7, lane * 8) = jpeg[i + lane];
            keep[lane] = 1;
        }
        if (static_cast<int>(beat) == bad_keep_beat) keep = 0xDu;
        w.keep = keep;
        w.last = (i + 4 >= jpeg.size()) || static_cast<int>(beat) == early_last_beat;
        in.write(w);
        if (static_cast<int>(beat) == early_last_beat) break;
    }
    result_t r;
    r.status = ~0u;
    jpeg_axis_gray(in, out, advertised_size, frame_id, r.status);
    r.input_empty = in.empty();
    while (!out.empty()) {
        const axis_word w = out.read();
        r.beats.push_back({static_cast<uint32_t>(w.data), static_cast<uint32_t>(w.keep), static_cast<bool>(w.last)});
    }
    return r;
}

static result_t invoke(const std::vector<uint8_t>& jpeg, int advertised_size,
                       int bad_keep_beat = -1, int early_last_beat = -1,
                       unsigned frame_id = 1) {
    hls::stream<axis_word> in("jpeg_axis_in"), out("gray_axis_out");
    return invoke_on(in, out, jpeg, advertised_size, bad_keep_beat, early_last_beat, frame_id);
}

static unsigned validate_packet(const result_t& r, const std::vector<uint8_t>& reference,
                                unsigned frame_id, const std::string& label,
                                const std::string& reference_kind) {
    require(reference_kind == "vendorY" || reference_kind == "opencv", label + ": reference kind must be vendorY or opencv");
    require(r.status == JPEG_GRAY_STATUS_OK, label + ": expected status 0, got " + std::to_string(r.status));
    const size_t pixels = JPEG_GRAY_PIXEL_COUNT;
    if (reference_kind == "vendorY")
        require(reference.size() == 512000, label + ": vendor planar reference must be 512000 bytes");
    else
        require(reference.size() == pixels, label + ": OpenCV Gray8 reference must be 256000 bytes");
    const size_t payload_beats = pixels / 4;
    const size_t expected_beats = 4 + payload_beats + 4;
    require(r.beats.size() == expected_beats, label + ": wrong AXIS beat count " + std::to_string(r.beats.size()));
    for (size_t i = 0; i < r.beats.size(); ++i) {
        require(r.beats[i].keep == 0xFu, label + ": output TKEEP != 0xF at beat " + std::to_string(i));
        require(r.beats[i].last == (i + 1 == r.beats.size()), label + ": TLAST is not asserted only on final beat");
    }
    require(r.beats[0].data == JPEG_GRAY_PACKET_MAGIC, label + ": bad packet magic");
    require(r.beats[1].data == ((JPEG_GRAY_HEIGHT << 16) | JPEG_GRAY_WIDTH), label + ": bad geometry");
    require(r.beats[2].data == JPEG_GRAY_PIXEL_COUNT, label + ": bad payload length");
    require(r.beats[3].data == frame_id, label + ": bad header frame ID");

    uint32_t checksum = 0;
    unsigned max_diff = 0;
    size_t threshold_count = 0;
    size_t threshold_reference_count = 0;
    size_t threshold_pixel_mismatches = 0;
    for (size_t i = 0; i < pixels; ++i) {
        const uint8_t value = static_cast<uint8_t>(r.beats[4 + i / 4].data >> ((i % 4) * 8));
        const uint8_t ref = reference[i];
        checksum += value;
        const unsigned diff = value > ref ? value - ref : ref - value;
        max_diff = std::max(max_diff, diff);
        const bool fg = value >= 128;
        const bool ref_fg = ref >= 128;
        threshold_count += fg;
        threshold_reference_count += ref_fg;
        threshold_pixel_mismatches += (fg != ref_fg);
    }
    const size_t tail = 4 + payload_beats;
    require(r.beats[tail + 0].data == JPEG_GRAY_PACKET_END_MAGIC, label + ": bad trailer magic");
    require(r.beats[tail + 1].data == frame_id, label + ": bad trailer frame ID");
    require(r.beats[tail + 2].data == checksum, label + ": bad payload checksum");
    require(r.beats[tail + 3].data == JPEG_GRAY_PACKET_END_GUARD, label + ": bad trailer guard");
    const unsigned max_allowed_diff = reference_kind == "vendorY" ? 0u : 1u;
    require(max_diff <= max_allowed_diff, label + ": " + reference_kind + " Y max absolute difference=" + std::to_string(max_diff));
    require(threshold_pixel_mismatches == 0, label + ": threshold-128 mask flips=" + std::to_string(threshold_pixel_mismatches));
    std::cout << "PASS " << label << " frame_id=" << frame_id << " max_y_diff=" << max_diff
              << " threshold128_count=" << threshold_count
              << " reference_threshold128_count=" << threshold_reference_count
              << " threshold_pixel_mismatches=" << threshold_pixel_mismatches
              << " checksum=" << checksum << " beats=" << expected_beats << "\n";
    return max_diff;
}

static void check_good(const std::vector<uint8_t>& jpeg, const std::vector<uint8_t>& ref,
                       unsigned frame_id, const std::string& label,
                       const std::string& reference_kind) {
    const result_t r = invoke(jpeg, static_cast<int>(jpeg.size()), -1, -1, frame_id);
    require(r.input_empty, label + ": DUT returned with unconsumed input AXIS beats");
    validate_packet(r, ref, frame_id, label, reference_kind);
}

static void check_rejected_output(const result_t& r, unsigned frame_id, const std::string& label) {
    require(r.status != JPEG_GRAY_STATUS_OK, label + ": expected nonzero status");
    require(r.input_empty, label + ": unread input prevents a recovery claim");
    if (r.beats.empty()) {
        std::cout << "REJECT_PACKET " << label << " absent=true\n";
        return;
    }
    const size_t tail = 4 + JPEG_GRAY_PIXEL_COUNT / 4;
    require(r.beats.size() == tail + 4, label + ": incomplete error packet");
    require(r.beats[0].data == JPEG_GRAY_PACKET_MAGIC && r.beats[1].data == ((JPEG_GRAY_HEIGHT << 16) | JPEG_GRAY_WIDTH)
            && r.beats[2].data == JPEG_GRAY_PIXEL_COUNT && r.beats[3].data == frame_id,
            label + ": invalid error packet header");
    uint32_t sum = 0;
    for (size_t i = 0; i < r.beats.size(); ++i) {
        require(r.beats[i].keep == 0xFu && r.beats[i].last == (i + 1 == r.beats.size()), label + ": malformed error AXIS packet");
        if (i >= 4 && i < tail) {
            for (unsigned lane = 0; lane < 4; ++lane) sum += uint8_t(r.beats[i].data >> (lane * 8));
        }
    }
    require(r.beats[tail].data == JPEG_GRAY_PACKET_END_MAGIC && r.beats[tail + 1].data == frame_id
            && r.beats[tail + 3].data == JPEG_GRAY_PACKET_END_GUARD, label + ": malformed error trailer");
    require(r.beats[tail + 2].data != sum, label + ": rejected packet checksum still validates");
    std::cout << "REJECT_PACKET " << label << " status=" << r.status << " checksum_invalid=true payload_sum=" << sum
              << " trailer_checksum=" << r.beats[tail + 2].data << "\n";
}

static bool set_sof(std::vector<uint8_t>& jpeg, uint8_t replacement_marker,
                    uint16_t height, uint16_t width) {
    if (jpeg.size() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8) return false;
    size_t i = 2;
    while (i + 3 < jpeg.size()) {
        if (jpeg[i] != 0xFF) { ++i; continue; }
        while (i < jpeg.size() && jpeg[i] == 0xFF) ++i;
        if (i >= jpeg.size()) return false;
        const uint8_t marker = jpeg[i++];
        if (marker == 0xDA || marker == 0xD9) return false;
        if (marker == 0xD8 || (marker >= 0xD0 && marker <= 0xD7) || marker == 0x01) continue;
        if (i + 1 >= jpeg.size()) return false;
        const uint16_t length = (uint16_t(jpeg[i]) << 8) | jpeg[i + 1];
        if (length < 2 || i + length > jpeg.size()) return false;
        if (marker >= 0xC0 && marker <= 0xCF && marker != 0xC4 && marker != 0xC8 && marker != 0xCC) {
            if (replacement_marker) jpeg[i - 1] = replacement_marker;
            if (height) { jpeg[i + 3] = uint8_t(height >> 8); jpeg[i + 4] = uint8_t(height); }
            if (width) { jpeg[i + 5] = uint8_t(width >> 8); jpeg[i + 6] = uint8_t(width); }
            return true;
        }
        i += length;
    }
    return false;
}

static bool find_sos_bounds(const std::vector<uint8_t>& jpeg, size_t& marker_pos,
                            size_t& entropy_start, size_t& entropy_end) {
    if (jpeg.size() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8) return false;
    size_t i = 2;
    while (i + 1 < jpeg.size()) {
        if (jpeg[i] != 0xFF) return false;
        marker_pos = i;
        while (i < jpeg.size() && jpeg[i] == 0xFF) ++i;
        if (i >= jpeg.size()) return false;
        const uint8_t marker = jpeg[i++];
        if (marker == 0xDA) {
            if (i + 1 >= jpeg.size()) return false;
            const size_t length = (size_t(jpeg[i]) << 8) | jpeg[i + 1];
            if (length < 2 || i + length > jpeg.size()) return false;
            entropy_start = i + length;
            entropy_end = jpeg.size();
            for (size_t j = entropy_start; j + 1 < jpeg.size(); ++j) {
                if (jpeg[j] == 0xFF && jpeg[j + 1] == 0xD9) { entropy_end = j; break; }
            }
            return entropy_start < entropy_end;
        }
        if (marker == 0xD9 || marker == 0xD8 || marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) continue;
        if (i + 1 >= jpeg.size()) return false;
        const size_t length = (size_t(jpeg[i]) << 8) | jpeg[i + 1];
        if (length < 2 || i + length > jpeg.size()) return false;
        i += length;
    }
    return false;
}

static void run_faults(const std::vector<uint8_t>& jpeg, const std::vector<uint8_t>& ref,
                       unsigned first_frame_id, const std::string& reference_kind) {
    size_t sos_marker = 0, entropy_start = 0, entropy_end = 0;
    require(find_sos_bounds(jpeg, sos_marker, entropy_start, entropy_end), "could not locate SOS and entropy bounds");
    unsigned next_good_id = first_frame_id;
    auto reject_then_recover = [&](const std::string& name, const std::vector<uint8_t>& bad,
                                   int size_arg, int bad_keep, int early_last, bool same_stream) {
        hls::stream<axis_word> reused_in("jpeg_axis_reuse_in"), reused_out("gray_axis_reuse_out");
        const result_t r = same_stream
            ? invoke_on(reused_in, reused_out, bad, size_arg, bad_keep, early_last, next_good_id)
            : invoke(bad, size_arg, bad_keep, early_last, next_good_id);
        require(r.status != JPEG_GRAY_STATUS_OK, name + ": malformed input returned status 0");
        require(r.input_empty, name + ": DUT returned with unconsumed input; cannot claim recovery without reset");
        check_rejected_output(r, next_good_id, name);
        std::cout << "PASS reject " << name << " status=" << r.status
                  << " input_empty=true output_beats=" << r.beats.size() << " (empty output allowed)\n";
        ++next_good_id;
        if (same_stream) {
            const result_t good = invoke_on(reused_in, reused_out, jpeg, static_cast<int>(jpeg.size()), -1, -1, next_good_id);
            require(good.input_empty, name + "/recovery: unconsumed good-frame input");
            validate_packet(good, ref, next_good_id, name + "/recovery/same-stream", reference_kind);
        } else {
            check_good(jpeg, ref, next_good_id, name + "/recovery", reference_kind);
        }
        ++next_good_id;
    };
    reject_then_recover("length-mismatch", jpeg, static_cast<int>(jpeg.size()) - 1, -1, -1, false);
    const int header_early_beat = static_cast<int>((sos_marker / 2) / 4);
    require(header_early_beat >= 0 && static_cast<size_t>(header_early_beat) * 4 < sos_marker,
            "cannot place TLAST inside JPEG header before SOS");
    reject_then_recover("early-tlast-header", jpeg, static_cast<int>(jpeg.size()), -1, header_early_beat, false);
    reject_then_recover("bad-keep", jpeg, static_cast<int>(jpeg.size()), 4, -1, false);
    std::vector<uint8_t> oversize = jpeg;
    oversize.resize(JPEG_GRAY_MAX_JPEG_BYTES + 1, 0);
    reject_then_recover("over-512000", oversize, static_cast<int>(oversize.size()), -1, -1, false);
    std::vector<uint8_t> no_eoi = jpeg;
    require(no_eoi.size() >= 2, "input too short for EOI mutation");
    no_eoi[no_eoi.size() - 2] = 0;
    no_eoi[no_eoi.size() - 1] = 0;
    reject_then_recover("missing-eoi", no_eoi, static_cast<int>(no_eoi.size()), -1, -1, true);
    std::vector<uint8_t> wrong_size = jpeg;
    require(set_sof(wrong_size, 0, 240, 320), "could not locate JPEG SOF for wrong-size case");
    reject_then_recover("wrong-dimensions", wrong_size, static_cast<int>(wrong_size.size()), -1, -1, false);
    std::vector<uint8_t> wrong_mode = jpeg;
    require(set_sof(wrong_mode, 0xC2, 0, 0), "could not locate JPEG SOF for wrong-mode case");
    reject_then_recover("wrong-mode-progressive", wrong_mode, static_cast<int>(wrong_mode.size()), -1, -1, false);
}

static void run_entropy_cut(const std::vector<uint8_t>& jpeg, const std::vector<uint8_t>& ref,
                            unsigned frame_id, const std::string& reference_kind) {
    size_t sos_marker = 0, entropy_start = 0, entropy_end = 0;
    require(find_sos_bounds(jpeg, sos_marker, entropy_start, entropy_end), "could not locate SOS and entropy bounds");
    const size_t cut_byte = entropy_start + (entropy_end - entropy_start) / 2;
    const int cut_beat = static_cast<int>(cut_byte / 4);
    require(cut_byte > entropy_start && cut_byte < entropy_end, "invalid entropy midpoint");
    std::cout << "entropy-cut: TLAST beat=" << cut_beat << " byte=" << cut_byte
              << " entropy_range=[" << entropy_start << "," << entropy_end << ")\n";
    hls::stream<axis_word> reused_in("entropy_reuse_in"), reused_out("entropy_reuse_out");
    const result_t r = invoke_on(reused_in, reused_out, jpeg, static_cast<int>(jpeg.size()), -1, cut_beat, frame_id);
    require(r.status != JPEG_GRAY_STATUS_OK, "entropy-cut input returned status 0");
    require(r.input_empty, "entropy-cut returned with unread input; reset/recovery is not claimed");
    check_rejected_output(r, frame_id, "entropy-cut");
    std::cout << "PASS reject early-tlast-after-sos status=" << r.status
              << " input_empty=true output_beats=" << r.beats.size() << " (empty output allowed)\n";
    const result_t good = invoke_on(reused_in, reused_out, jpeg, static_cast<int>(jpeg.size()), -1, -1, frame_id + 1);
    require(good.input_empty, "entropy-cut/recovery: unread input on reused stream");
    validate_packet(good, ref, frame_id + 1, "entropy-cut/recovery/same-stream", reference_kind);
}

int main(int argc, char** argv) {
    try {
        require(argc == 7, "usage: jpeg_axis_gray_tb <smoke|faults|entropy-cut|align|batch> <input-or-dir> <reference-or-dir> <start-frame> <end-frame> <vendorY|opencv>");
        const std::string mode(argv[1]);
        const std::string input_path(argv[2]);
        const std::string reference_path(argv[3]);
        const unsigned first = static_cast<unsigned>(std::stoul(argv[4]));
        const unsigned last = static_cast<unsigned>(std::stoul(argv[5]));
        const std::string reference_kind(argv[6]);
        if (mode == "smoke") {
            check_good(read_file(input_path), read_file(reference_path), first, "smoke", reference_kind);
        } else if (mode == "faults") {
            run_faults(read_file(input_path), read_file(reference_path), first, reference_kind);
        } else if (mode == "entropy-cut") {
            run_entropy_cut(read_file(input_path), read_file(reference_path), first, reference_kind);
        } else if (mode == "align") {
            const std::vector<uint8_t> ref = read_file(reference_path);
            for (unsigned residue = 0; residue < last; ++residue) {
                const std::string path = input_path + "/" + frame_stem(first) + "_tailmod" + std::to_string(residue) + ".jpg";
                check_good(read_file(path), ref, first, "tail-align-mod" + std::to_string(residue), reference_kind);
            }
        } else if (mode == "batch") {
            for (unsigned id = first; id <= last; ++id) {
                const std::string stem = frame_stem(id);
                const auto jpeg = read_file(input_path + "/" + stem + ".jpg");
                const auto ref = read_file(reference_path + "/" + stem + ".gray8");
                check_good(jpeg, ref, id, "batch-" + stem, reference_kind);
            }
        } else {
            throw std::runtime_error("unknown mode: " + mode);
        }
        std::cout << "ALL REQUESTED CASES PASS\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAIL: " << e.what() << "\n";
        return 1;
    }
}
