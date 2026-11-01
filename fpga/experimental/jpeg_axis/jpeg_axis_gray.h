#ifndef JPEG_AXIS_GRAY_H
#define JPEG_AXIS_GRAY_H

#include <ap_axi_sdata.h>
#include <hls_stream.h>

// Gray8_PACKET_V1 words; each protocol word is one 32-bit AXIS beat with TKEEP=0xF.
#define JPEG_GRAY_PACKET_MAGIC 0x47525938u
#define JPEG_GRAY_PACKET_END_MAGIC 0x454e4438u
#define JPEG_GRAY_PACKET_END_GUARD 0xc0dec018u
#define JPEG_GRAY_WIDTH 640u
#define JPEG_GRAY_HEIGHT 400u
#define JPEG_GRAY_PIXEL_COUNT (JPEG_GRAY_WIDTH * JPEG_GRAY_HEIGHT)
#define JPEG_GRAY_MAX_JPEG_BYTES 512000u
#define JPEG_GRAY_MAX_HEADER_BYTES 4096u

// Nonzero status indicates a rejected or malformed input frame. Bits are stable
// for the wrapper/testbench contract and may be ORed when multiple checks fail.
enum jpeg_axis_gray_status {
    JPEG_GRAY_STATUS_OK = 0u,
    JPEG_GRAY_STATUS_SIZE = 1u << 0,
    JPEG_GRAY_STATUS_AXIS_KEEP = 1u << 1,
    JPEG_GRAY_STATUS_AXIS_LAST = 1u << 2,
    JPEG_GRAY_STATUS_HEADER_LIMIT = 1u << 3,
    JPEG_GRAY_STATUS_JPEG_STRUCTURE = 1u << 4,
    JPEG_GRAY_STATUS_JPEG_EOI = 1u << 5,
    JPEG_GRAY_STATUS_ENTROPY_LENGTH = 1u << 6
};

void jpeg_axis_gray(hls::stream<ap_axiu<32, 0, 0, 0> >& jpeg_axis_in,
                    hls::stream<ap_axiu<32, 0, 0, 0> >& gray_axis_out,
                    int jpeg_size,
                    unsigned frame_id,
                    unsigned& status);

#endif // JPEG_AXIS_GRAY_H

