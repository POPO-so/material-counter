#include "jpeg_axis_gray.h"

#include "XAcc_jfifparser.hpp"
#include "XAcc_idct.hpp"

#include <stdint.h>

namespace {

static const unsigned MCU_COLUMNS = 40;
static const unsigned MCU_ROWS = 50;
static const unsigned MCU_COUNT = MCU_COLUMNS * MCU_ROWS;
static const unsigned BLOCK_COUNT = MCU_COUNT * 4;
static const unsigned HEADER_WORDS = JPEG_GRAY_MAX_HEADER_BYTES / 2;

typedef ap_axiu<32, 0, 0, 0> axis_word_t;
typedef CHType jpeg_ch_t;

struct axis_reader {
    ap_uint<32> data;
    unsigned lane;
    unsigned lane_count;
    unsigned bytes_seen;
    bool word_last;
    bool have_word;
    bool saw_last;
    unsigned status;
};

static inline void put_header_byte(ap_uint<16> header[HEADER_WORDS], unsigned index, uint8_t value) {
#pragma HLS INLINE
    if ((index & 1u) == 0) header[index >> 1].range(7, 0) = value;
    else header[index >> 1].range(15, 8) = value;
}

static inline uint8_t get_header_byte(const ap_uint<16> header[HEADER_WORDS], unsigned index) {
#pragma HLS INLINE
    ap_uint<16> word = header[index >> 1];
    return (index & 1u) ? (uint8_t)word.range(15, 8) : (uint8_t)word.range(7, 0);
}

static inline void init_reader(axis_reader& state) {
#pragma HLS INLINE
    state.data = 0;
    state.lane = 0;
    state.lane_count = 0;
    state.bytes_seen = 0;
    state.word_last = false;
    state.have_word = false;
    state.saw_last = false;
    state.status = JPEG_GRAY_STATUS_OK;
}

static void get_axis_word(hls::stream<axis_word_t>& input, axis_reader& state, unsigned expected_size) {
#pragma HLS INLINE
    axis_word_t word = input.read();
    state.data = word.data;
    state.word_last = word.last;
    state.lane = 0;
    state.lane_count = 0;
    state.have_word = true;

    bool saw_zero = false;
    unsigned valid_count = 0;
    for (unsigned lane = 0; lane < 4; ++lane) {
#pragma HLS UNROLL
        const bool valid = (bool)word.keep[lane];
        if (valid) {
            ++valid_count;
            if (saw_zero) state.status |= JPEG_GRAY_STATUS_AXIS_KEEP;
            else ++state.lane_count;
        } else {
            saw_zero = true;
        }
    }
    state.bytes_seen += valid_count;
    if (valid_count == 0 || (!word.last && valid_count != 4)) {
        state.status |= JPEG_GRAY_STATUS_AXIS_KEEP;
    }
    if (word.last) {
        if ((int)state.bytes_seen != (int)expected_size) {
            state.status |= JPEG_GRAY_STATUS_AXIS_LAST | JPEG_GRAY_STATUS_SIZE;
        }
    } else if (state.bytes_seen >= expected_size) {
        state.status |= JPEG_GRAY_STATUS_AXIS_LAST;
    }
    if (state.bytes_seen > JPEG_GRAY_MAX_JPEG_BYTES) state.status |= JPEG_GRAY_STATUS_SIZE;
}

static bool get_axis_byte(hls::stream<axis_word_t>& input,
                          axis_reader& state,
                          unsigned expected_size,
                          uint8_t& value) {
#pragma HLS INLINE
READ_BYTE_LOOP:
    while (true) {
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 512000
        if (state.have_word && state.lane < state.lane_count) {
            ap_uint<8> byte_value = state.data.range(state.lane * 8 + 7, state.lane * 8);
            value = (uint8_t)byte_value;
            ++state.lane;
            return true;
        }
        if (state.have_word) {
            const bool ended = state.word_last;
            state.have_word = false;
            if (ended) {
                state.saw_last = true;
                return false;
            }
        }
        if (state.saw_last) return false;
        get_axis_word(input, state, expected_size);
    }
}

static void drain_axis(hls::stream<axis_word_t>& input, axis_reader& state, unsigned expected_size) {
#pragma HLS INLINE off
DRAIN_TO_TLAST:
    while (!state.saw_last) {
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 128000
        if (state.have_word) {
            if (state.word_last) {
                state.have_word = false;
                state.saw_last = true;
                break;
            }
            state.have_word = false;
        }
        get_axis_word(input, state, expected_size);
        if (state.have_word && state.word_last && state.lane_count == 0) {
            state.have_word = false;
            state.saw_last = true;
        }
    }
    if ((int)state.bytes_seen != (int)expected_size) state.status |= JPEG_GRAY_STATUS_SIZE;
}

static bool cache_byte(hls::stream<axis_word_t>& input,
                       axis_reader& reader,
                       unsigned expected_size,
                       ap_uint<16> header[HEADER_WORDS],
                       unsigned& header_size) {
#pragma HLS INLINE
    if (header_size >= JPEG_GRAY_MAX_HEADER_BYTES) return false;
    uint8_t value = 0;
    if (!get_axis_byte(input, reader, expected_size, value)) return false;
    put_header_byte(header, header_size++, value);
    return true;
}

static bool read_header(hls::stream<axis_word_t>& input,
                        axis_reader& reader,
                        unsigned expected_size,
                        ap_uint<16> header[HEADER_WORDS],
                        unsigned& header_size,
                        unsigned& status) {
#pragma HLS INLINE off
    header_size = 0;
    if (!cache_byte(input, reader, expected_size, header, header_size) ||
        !cache_byte(input, reader, expected_size, header, header_size)) {
        status |= (header_size >= JPEG_GRAY_MAX_HEADER_BYTES) ? JPEG_GRAY_STATUS_HEADER_LIMIT
                                                              : JPEG_GRAY_STATUS_AXIS_LAST;
        return false;
    }
    if (get_header_byte(header, 0) != 0xFF || get_header_byte(header, 1) != 0xD8) {
        status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
        return false;
    }

READ_MARKERS:
    for (unsigned marker_count = 0; marker_count < JPEG_GRAY_MAX_HEADER_BYTES; ++marker_count) {
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 4096
        if (!cache_byte(input, reader, expected_size, header, header_size)) {
            status |= (header_size >= JPEG_GRAY_MAX_HEADER_BYTES) ? JPEG_GRAY_STATUS_HEADER_LIMIT
                                                                  : JPEG_GRAY_STATUS_AXIS_LAST;
            return false;
        }
        if (get_header_byte(header, header_size - 1) != 0xFF) {
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        }
        if (!cache_byte(input, reader, expected_size, header, header_size)) {
            status |= JPEG_GRAY_STATUS_AXIS_LAST;
            return false;
        }
        const uint8_t marker = get_header_byte(header, header_size - 1);
        // The pinned parser expects one 0xFF prefix. Reject fill runs and
        // standalone markers here; they are outside this experiment's subset.
        if (marker == 0xFF || marker == 0x00 || marker == 0xD8 || marker == 0xD9 ||
            (marker >= 0xD0 && marker <= 0xD7)) {
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        }
        if (!cache_byte(input, reader, expected_size, header, header_size) ||
            !cache_byte(input, reader, expected_size, header, header_size)) {
            status |= JPEG_GRAY_STATUS_AXIS_LAST;
            return false;
        }
        const unsigned segment_length =
            ((unsigned)get_header_byte(header, header_size - 2) << 8) | get_header_byte(header, header_size - 1);
        if (segment_length < 2 || header_size + segment_length - 2 > JPEG_GRAY_MAX_HEADER_BYTES) {
            status |= (segment_length < 2) ? JPEG_GRAY_STATUS_JPEG_STRUCTURE : JPEG_GRAY_STATUS_HEADER_LIMIT;
            return false;
        }
        for (unsigned i = 2; i < segment_length; ++i) {
#pragma HLS LOOP_TRIPCOUNT min = 0 max = 65533
            if (!cache_byte(input, reader, expected_size, header, header_size)) {
                status |= JPEG_GRAY_STATUS_AXIS_LAST;
                return false;
            }
        }
        if (marker == 0xDA) return true;
    }
    status |= JPEG_GRAY_STATUS_HEADER_LIMIT;
    return false;
}

static bool validate_header(const ap_uint<16> header[HEADER_WORDS], unsigned size, unsigned& status) {
#pragma HLS INLINE off
    bool saw_sof = false;
    bool saw_dri = false;
    bool saw_sos = false;
    bool dqt[2] = {false, false};
    bool dht[2][2] = {{false, false}, {false, false}};
    uint8_t component_id[3] = {0, 0, 0};
    unsigned offset = 2;

    if (size < 4 || get_header_byte(header, 0) != 0xFF || get_header_byte(header, 1) != 0xD8) {
        status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
        return false;
    }

VALIDATE_MARKERS:
    while (offset < size && !saw_sos) {
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 4096
        if (offset + 4 > size || get_header_byte(header, offset) != 0xFF) {
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        }
        const uint8_t marker = get_header_byte(header, offset + 1);
        const unsigned segment_length = ((unsigned)get_header_byte(header, offset + 2) << 8) |
                                        get_header_byte(header, offset + 3);
        if (marker == 0xFF || marker == 0x00 || marker == 0xD8 || marker == 0xD9 ||
            (marker >= 0xD0 && marker <= 0xD7) || segment_length < 2 ||
            offset + 2 + segment_length > size) {
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        }
        const unsigned body = offset + 4;
        const unsigned body_size = segment_length - 2;
        const unsigned end = body + body_size;

        if (marker == 0xFE) {
            // The pinned parser does not skip COM; keep the accepted subset explicit.
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        } else if ((marker & 0xF0) == 0xE0) {
            // APP0..APP15 are skipped by the pinned parser.
        } else if (marker == 0xDB) {
            unsigned cursor = body;
            while (cursor < end) {
                const uint8_t info = get_header_byte(header, cursor++);
                const unsigned precision = info >> 4;
                const unsigned table_id = info & 0x0F;
                if (precision != 0 || table_id > 1 || cursor + 64 > end) {
                    status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                    return false;
                }
                for (unsigned i = 0; i < 64; ++i) {
#pragma HLS PIPELINE II = 1
                    if (get_header_byte(header, cursor + i) == 0) {
                        status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                        return false;
                    }
                }
                cursor += 64;
                dqt[table_id] = true;
            }
        } else if (marker == 0xC4) {
            unsigned cursor = body;
            while (cursor < end) {
                const uint8_t info = get_header_byte(header, cursor++);
                const unsigned table_class = info >> 4;
                const unsigned table_id = info & 0x0F;
                if (table_class > 1 || table_id > 1 || cursor + 16 > end) {
                    status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                    return false;
                }
                unsigned symbols = 0;
                for (unsigned i = 0; i < 16; ++i) {
#pragma HLS PIPELINE II = 1
                    symbols += get_header_byte(header, cursor + i);
                }
                cursor += 16;
                if (symbols == 0 || symbols > (table_class ? 165u : 12u) || cursor + symbols > end) {
                    status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                    return false;
                }
                cursor += symbols;
                dht[table_class][table_id] = true;
            }
        } else if (marker == 0xC0) {
            if (saw_sof || segment_length != 17 || body_size != 15 ||
                get_header_byte(header, body) != 8 ||
                (((unsigned)get_header_byte(header, body + 1) << 8) | get_header_byte(header, body + 2)) !=
                    JPEG_GRAY_HEIGHT ||
                (((unsigned)get_header_byte(header, body + 3) << 8) | get_header_byte(header, body + 4)) !=
                    JPEG_GRAY_WIDTH ||
                get_header_byte(header, body + 5) != 3) {
                status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                return false;
            }
            for (unsigned c = 0; c < 3; ++c) {
#pragma HLS PIPELINE II = 1
                const unsigned cpos = body + 6 + c * 3;
                component_id[c] = get_header_byte(header, cpos);
                const uint8_t sampling = get_header_byte(header, cpos + 1);
                const uint8_t quant_id = get_header_byte(header, cpos + 2);
                if ((c == 0 && (sampling != 0x21 || quant_id != 0)) ||
                    (c != 0 && (sampling != 0x11 || quant_id != 1))) {
                    status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                    return false;
                }
            }
            if (component_id[0] == component_id[1] || component_id[0] == component_id[2] ||
                component_id[1] == component_id[2]) {
                status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                return false;
            }
            saw_sof = true;
        } else if (marker == 0xDD) {
            const unsigned restart_interval = (body_size == 2)
                                                  ? (((unsigned)get_header_byte(header, body) << 8) |
                                                     get_header_byte(header, body + 1))
                                                  : 0;
            if (saw_dri || body_size != 2 || restart_interval != 40) {
                status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                return false;
            }
            saw_dri = true;
        } else if (marker == 0xDA) {
            if (!saw_sof || saw_sos || segment_length != 12 || body_size != 10 ||
                get_header_byte(header, body) != 3) {
                status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                return false;
            }
            for (unsigned c = 0; c < 3; ++c) {
#pragma HLS PIPELINE II = 1
                const unsigned cpos = body + 1 + c * 2;
                const uint8_t expected_table = c ? 1 : 0;
                const uint8_t table_pair = get_header_byte(header, cpos + 1);
                if (get_header_byte(header, cpos) != component_id[c] ||
                    (table_pair >> 4) != expected_table || (table_pair & 0x0F) != expected_table) {
                    status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                    return false;
                }
            }
            if (get_header_byte(header, body + 7) != 0 || get_header_byte(header, body + 8) != 63 ||
                get_header_byte(header, body + 9) != 0) {
                status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
                return false;
            }
            saw_sos = true;
        } else {
            status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
            return false;
        }
        offset += 2 + segment_length;
    }

    if (offset != size || !saw_sof || !saw_sos || !saw_dri || !dqt[0] || !dqt[1] ||
        !dht[0][0] || !dht[0][1] || !dht[1][0] || !dht[1][1]) {
        status |= JPEG_GRAY_STATUS_JPEG_STRUCTURE;
        return false;
    }
    return true;
}

static void feed_entropy(axis_reader reader,
                         hls::stream<axis_word_t>& input,
                         unsigned expected_size,
                         hls::stream<jpeg_ch_t>& image_stream,
                         hls::stream<bool>& eof_stream,
                         hls::stream<unsigned>& status_stream) {
#pragma HLS INLINE off
    uint8_t pending = 0;
    bool has_pending = false;
    uint8_t previous = 0;
    uint8_t last = 0;
    bool have_byte = false;
    bool saw_eoi = false;
    bool after_eoi = false;
    unsigned bytes_after_eoi = 0;

FEED_BYTES:
    while (true) {
#pragma HLS LOOP_TRIPCOUNT min = 1 max = 512000
        uint8_t value = 0;
        if (!get_axis_byte(input, reader, expected_size, value)) break;
        const bool eoi_pair = have_byte && previous == 0xFF && value == 0xD9;
        if (after_eoi) ++bytes_after_eoi;
        if (eoi_pair) {
            saw_eoi = true;
            after_eoi = true;
        }
        if (!after_eoi || eoi_pair) {
            if (!has_pending) {
                pending = value;
                has_pending = true;
            } else {
                eof_stream.write(false);
                image_stream.write((jpeg_ch_t)((unsigned)pending | ((unsigned)value << 8)));
                has_pending = false;
            }
        }
        previous = value;
        last = value;
        have_byte = true;
    }
    if (has_pending) {
        eof_stream.write(false);
        image_stream.write((jpeg_ch_t)pending);
    }
    eof_stream.write(true);

    if ((int)reader.bytes_seen != (int)expected_size) reader.status |= JPEG_GRAY_STATUS_SIZE;
    if (!reader.saw_last) reader.status |= JPEG_GRAY_STATUS_AXIS_LAST;
    if (!saw_eoi || !have_byte || last != 0xD9 || previous != 0xD9 || bytes_after_eoi != 0) {
        reader.status |= JPEG_GRAY_STATUS_JPEG_EOI;
    }
    status_stream.write(reader.status);
}

static void decode_mcus(hls::stream<jpeg_ch_t>& image_stream,
                        hls::stream<bool>& eof_stream,
                        const uint16_t dht_table[2][2][1 << DHT1],
                        const uint8_t ac_values[2][165],
                        const HCODE_T ac_start_code[2][AC_N],
                        const int16_t ac_start_addr[2][16],
                        const uint8_t dc_values[2][12],
                        const HCODE_T dc_start_code[2][DC_N],
                        const int16_t dc_start_addr[2][16],
                        ap_uint<12> component_schedule,
                        const uint8_t blocks_per_component[3],
                        uint8_t scan_components,
                        uint16_t mcu_columns,
                        uint32_t mcu_count,
                        hls::stream<ap_uint<24> >& block_stream,
                        hls::stream<int>& decoder_status_stream) {
#pragma HLS INLINE off
    int decoder_status = 0;
    uint32_t restart_count = 0;
    xf::codec::details::mcu_decoder(image_stream, eof_stream, dht_table, ac_values, ac_start_code,
                                    ac_start_addr, dc_values, dc_start_code, dc_start_addr,
                                    component_schedule, scan_components, blocks_per_component,
                                    mcu_columns, mcu_count, decoder_status, restart_count, block_stream);
    (void)restart_count; // Existing Huffman logic tracks RST; cadence is not checked here.
    decoder_status_stream.write(decoder_status);
}

// The decoder status token is also its completion token: it is written only
// after every coefficient write has completed. Do not turn this stream into a
// scalar input to the guard or pixel sink; that would defer their start until
// the decoder finishes and can deadlock a bounded coefficient FIFO.
static void guard_coefficients(hls::stream<ap_uint<24> >& decoded_blocks,
                                hls::stream<int>& decoder_done,
                                hls::stream<ap_uint<24> >& guarded_blocks,
                                hls::stream<unsigned>& status_stream) {
#pragma HLS INLINE off
    unsigned completed_blocks = 0;
    unsigned status = JPEG_GRAY_STATUS_OK;
    bool saw_done = false;

GUARD_DECODED_COEFFICIENTS:
    while (true) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = 8000 max = 512000
        ap_uint<24> coefficient;
        // Always drain coefficients before consuming completion. Check again
        // on the iteration after completion: the independent FIFOs can expose
        // their tokens at different cycles, but completion follows all writes.
        if (decoded_blocks.read_nb(coefficient)) {
            if (completed_blocks < BLOCK_COUNT) {
                guarded_blocks.write(coefficient);
                if (coefficient[22]) ++completed_blocks;
            } else {
                // Consume excess coefficients without letting fixed-size IDCT
                // stop the producer and suppress its completion/error token.
                status |= JPEG_GRAY_STATUS_ENTROPY_LENGTH;
            }
        } else if (saw_done) {
            break;
        } else {
            int decoder_status = 0;
            if (decoder_done.read_nb(decoder_status)) {
                saw_done = true;
                if (decoder_status != 0) status |= JPEG_GRAY_STATUS_ENTROPY_LENGTH;
            }
        }
    }

    if (completed_blocks != BLOCK_COUNT) status |= JPEG_GRAY_STATUS_ENTROPY_LENGTH;
    ap_uint<24> zero_eob = 0;
    zero_eob[22] = 1;
    zero_eob.range(21, 16) = 63;
FILL_MISSING_BLOCKS:
    for (unsigned block = completed_blocks; block < BLOCK_COUNT; ++block) {
#pragma HLS PIPELINE II = 1
#pragma HLS LOOP_TRIPCOUNT min = 0 max = 8000
        // cache_mcu starts each block at zero. The first EOB closes any partial
        // block already forwarded; later EOB-only blocks have all-zero DCTs.
        // These pixels serve only to finish an invalid packet, never as a good
        // recovered image. No codec arithmetic is changed for valid input.
        guarded_blocks.write(zero_eob);
    }
    status_stream.write(status);
}

static void reorder_mcus(hls::stream<ap_uint<24> >& block_stream,
                         const uint8_t quant_tables[2][8][8],
                         const xf::codec::bas_info& base_info,
                         hls::stream<xf::codec::idct_out_t> pixels[8]) {
#pragma HLS INLINE off
    xf::codec::details::mcu_reorder(block_stream, quant_tables, base_info, pixels);
}

static inline void write_axis(hls::stream<axis_word_t>& output, uint32_t value, bool last) {
#pragma HLS INLINE
    axis_word_t word;
    word.data = value;
    word.keep = 0xF;
    word.strb = 0xF;
    word.last = last;
    output.write(word);
}

static void render_gray(hls::stream<xf::codec::idct_out_t> pixels[8],
                        hls::stream<unsigned>& input_status_stream,
                        hls::stream<unsigned>& coefficient_status_stream,
                        hls::stream<axis_word_t>& output,
                        unsigned frame_id,
                        unsigned& final_status) {
#pragma HLS INLINE off
    uint8_t strip[8][JPEG_GRAY_WIDTH];
#pragma HLS BIND_STORAGE variable = strip type = ram_1p impl = bram
    uint32_t checksum = 0;
    unsigned status = JPEG_GRAY_STATUS_OK;

    write_axis(output, JPEG_GRAY_PACKET_MAGIC, false);
    write_axis(output, (JPEG_GRAY_HEIGHT << 16) | JPEG_GRAY_WIDTH, false);
    write_axis(output, JPEG_GRAY_PIXEL_COUNT, false);
    write_axis(output, frame_id, false);

    for (unsigned mcu_y = 0; mcu_y < MCU_ROWS; ++mcu_y) {
        for (unsigned mcu_x = 0; mcu_x < MCU_COLUMNS; ++mcu_x) {
            // MCU block scan order for 4:2:2: Y-left, Y-right, Cb, Cr.
            for (unsigned y_block = 0; y_block < 2; ++y_block) {
                for (unsigned x = 0; x < 8; ++x) {
#pragma HLS PIPELINE II = 1
                    for (unsigned row = 0; row < 8; ++row) {
#pragma HLS UNROLL
                        strip[row][mcu_x * 16 + y_block * 8 + x] = pixels[row].read();
                    }
                }
            }
            for (unsigned chroma = 0; chroma < 2; ++chroma) {
                for (unsigned x = 0; x < 8; ++x) {
#pragma HLS PIPELINE II = 1
                    for (unsigned row = 0; row < 8; ++row) {
#pragma HLS UNROLL
                        (void)pixels[row].read();
                    }
                }
            }
        }
        for (unsigned row = 0; row < 8; ++row) {
            for (unsigned x = 0; x < JPEG_GRAY_WIDTH; x += 4) {
#pragma HLS PIPELINE II = 1
                const uint8_t p0 = strip[row][x];
                const uint8_t p1 = strip[row][x + 1];
                const uint8_t p2 = strip[row][x + 2];
                const uint8_t p3 = strip[row][x + 3];
                checksum += p0 + p1 + p2 + p3;
                const uint32_t packed = (uint32_t)p0 | ((uint32_t)p1 << 8) |
                                        ((uint32_t)p2 << 16) | ((uint32_t)p3 << 24);
                write_axis(output, packed, false);
            }
        }
    }

    status |= input_status_stream.read();
    status |= coefficient_status_stream.read(); // Read only after all pixels have drained.
    const uint32_t packet_checksum = checksum + ((status != JPEG_GRAY_STATUS_OK) ? 1u : 0u);
    write_axis(output, JPEG_GRAY_PACKET_END_MAGIC, false);
    write_axis(output, frame_id, false);
    write_axis(output, packet_checksum, false);
    write_axis(output, JPEG_GRAY_PACKET_END_GUARD, true);
    final_status = status;
}

static void run_pipeline(axis_reader reader,
                         hls::stream<axis_word_t>& input,
                         unsigned jpeg_size,
                         unsigned frame_id,
                         const uint16_t dht_table[2][2][1 << DHT1],
                         const uint8_t ac_values[2][165],
                         const HCODE_T ac_start_code[2][AC_N],
                         const int16_t ac_start_addr[2][16],
                         const uint8_t dc_values[2][12],
                         const HCODE_T dc_start_code[2][DC_N],
                         const int16_t dc_start_addr[2][16],
                         ap_uint<12> component_schedule,
                         const uint8_t blocks_per_component[3],
                         uint8_t scan_components,
                         uint16_t mcu_columns,
                         uint32_t mcu_count,
                         const uint8_t quant_tables[2][8][8],
                         const xf::codec::bas_info& base_info,
                         hls::stream<axis_word_t>& output,
                         unsigned& final_status) {
#pragma HLS INLINE off
    hls::stream<jpeg_ch_t> image_stream;
#pragma HLS STREAM variable = image_stream depth = 64
    hls::stream<bool> eof_stream;
#pragma HLS STREAM variable = eof_stream depth = 64
    hls::stream<ap_uint<24> > decoded_block_stream;
#pragma HLS STREAM variable = decoded_block_stream depth = 64
#pragma HLS BIND_STORAGE variable = decoded_block_stream type = fifo impl = lutram
    hls::stream<ap_uint<24> > block_stream;
#pragma HLS STREAM variable = block_stream depth = 1024
#pragma HLS BIND_STORAGE variable = block_stream type = fifo impl = bram
    hls::stream<xf::codec::idct_out_t> pixels[8];
#pragma HLS STREAM variable = pixels depth = 256
#pragma HLS ARRAY_PARTITION variable = pixels complete dim = 0
#pragma HLS BIND_STORAGE variable = pixels type = fifo impl = bram
    hls::stream<unsigned> input_status_stream;
#pragma HLS STREAM variable = input_status_stream depth = 1
    hls::stream<int> decoder_status_stream;
#pragma HLS STREAM variable = decoder_status_stream depth = 1
    hls::stream<unsigned> coefficient_status_stream;
#pragma HLS STREAM variable = coefficient_status_stream depth = 1

#pragma HLS DATAFLOW
    feed_entropy(reader, input, jpeg_size, image_stream, eof_stream, input_status_stream);
    decode_mcus(image_stream, eof_stream, dht_table, ac_values, ac_start_code, ac_start_addr,
                dc_values, dc_start_code, dc_start_addr, component_schedule, blocks_per_component,
                scan_components, mcu_columns, mcu_count, decoded_block_stream, decoder_status_stream);
    guard_coefficients(decoded_block_stream, decoder_status_stream, block_stream, coefficient_status_stream);
    reorder_mcus(block_stream, quant_tables, base_info, pixels);
    render_gray(pixels, input_status_stream, coefficient_status_stream, output, frame_id, final_status);
}

} // namespace

void jpeg_axis_gray(hls::stream<ap_axiu<32, 0, 0, 0> >& jpeg_axis_in,
                    hls::stream<ap_axiu<32, 0, 0, 0> >& gray_axis_out,
                    int jpeg_size,
                    unsigned frame_id,
                    unsigned& status) {
#pragma HLS INTERFACE axis port = jpeg_axis_in
#pragma HLS INTERFACE axis port = gray_axis_out
#pragma HLS INTERFACE s_axilite port = jpeg_size bundle = control
#pragma HLS INTERFACE s_axilite port = frame_id bundle = control
#pragma HLS INTERFACE s_axilite port = status bundle = control
#pragma HLS INTERFACE s_axilite port = return bundle = control

    ap_uint<16> header[HEADER_WORDS];
#pragma HLS BIND_STORAGE variable = header type = ram_1p impl = bram
    axis_reader reader;
    init_reader(reader);

    if (jpeg_size <= 0 || (unsigned)jpeg_size > JPEG_GRAY_MAX_JPEG_BYTES) {
        reader.status |= JPEG_GRAY_STATUS_SIZE;
        drain_axis(jpeg_axis_in, reader, jpeg_size > 0 ? (unsigned)jpeg_size : 0);
        status = reader.status;
        return;
    }

    unsigned header_size = 0;
    unsigned header_status = JPEG_GRAY_STATUS_OK;
    if (!read_header(jpeg_axis_in, reader, (unsigned)jpeg_size, header, header_size, header_status)) {
        drain_axis(jpeg_axis_in, reader, (unsigned)jpeg_size);
        status = header_status | reader.status;
        if (status == JPEG_GRAY_STATUS_OK) status = JPEG_GRAY_STATUS_JPEG_STRUCTURE;
        return;
    }
    if (reader.status != JPEG_GRAY_STATUS_OK) {
        drain_axis(jpeg_axis_in, reader, (unsigned)jpeg_size);
        status = reader.status;
        return;
    }
    if (!validate_header(header, header_size, header_status)) {
        drain_axis(jpeg_axis_in, reader, (unsigned)jpeg_size);
        status = header_status | reader.status;
        return;
    }

    uint16_t dht_table[2][2][1 << DHT1] = {};
    uint8_t ac_values[2][165] = {};
    HCODE_T ac_start_code[2][AC_N] = {};
    int16_t ac_start_addr[2][16] = {};
    uint8_t dc_values[2][12] = {};
    HCODE_T dc_start_code[2][DC_N] = {};
    int16_t dc_start_addr[2][16] = {};
    ap_uint<12> component_schedule = 0;
    int left = 0;
    uint8_t blocks_per_component[3] = {};
    uint8_t quant_tables[2][8][8] = {};
    int parser_status = 0;
    xf::codec::img_info image_info = {};
    xf::codec::cmp_info component_info[3] = {};
    xf::codec::bas_info base_info = {};
    int byte_row = 0;
    int byte_column = 0;

#pragma HLS ARRAY_PARTITION variable = dht_table complete dim = 1
#pragma HLS ARRAY_PARTITION variable = dht_table complete dim = 2
#pragma HLS BIND_STORAGE variable = dht_table type = ram_2p impl = bram
#pragma HLS BIND_STORAGE variable = ac_values type = ram_2p impl = lutram
#pragma HLS ARRAY_PARTITION variable = ac_start_code complete dim = 2
#pragma HLS BIND_STORAGE variable = ac_start_addr type = ram_2p impl = lutram
#pragma HLS BIND_STORAGE variable = dc_values type = ram_2p impl = lutram
#pragma HLS ARRAY_PARTITION variable = dc_start_code complete dim = 2
#pragma HLS BIND_STORAGE variable = dc_start_addr type = ram_2p impl = lutram
#pragma HLS ARRAY_PARTITION variable = blocks_per_component complete dim = 1
#pragma HLS ARRAY_PARTITION variable = quant_tables complete dim = 3

    xf::codec::details::parser_jpg_top(header, jpeg_size, byte_row, byte_column, dht_table,
                                       ac_values, ac_start_code, ac_start_addr,
                                       dc_values, dc_start_code, dc_start_addr,
                                       component_schedule, left, blocks_per_component,
                                       quant_tables, parser_status, image_info,
                                       component_info, base_info);
    if (parser_status != 0 || left != jpeg_size - (int)header_size ||
        image_info.hls_mcuh != MCU_COLUMNS || image_info.hls_mcuv != MCU_ROWS ||
        image_info.hls_mcuc != MCU_COUNT || base_info.mcu_cmp != 4) {
        drain_axis(jpeg_axis_in, reader, (unsigned)jpeg_size);
        status = header_status | reader.status | JPEG_GRAY_STATUS_JPEG_STRUCTURE;
        return;
    }

    unsigned final_status = JPEG_GRAY_STATUS_OK;
    run_pipeline(reader, jpeg_axis_in, (unsigned)jpeg_size, frame_id,
                 dht_table, ac_values, ac_start_code, ac_start_addr,
                 dc_values, dc_start_code, dc_start_addr, component_schedule,
                 blocks_per_component, image_info.hls_cs_cmpc, image_info.hls_mcuh,
                 image_info.hls_mcuc, quant_tables, base_info, gray_axis_out, final_status);
    status = final_status;
}
