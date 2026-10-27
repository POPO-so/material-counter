`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         material-counter project
// Engineer:        WO-0014 maintainers
//
// Create Date:     2026/10/27
// Design Name:     Gray8 packet adapter
// Module Name:     axis_dma_gray_adapter
// Description:     description/axis_dma_gray_adapter_Design.pdf
// Simulations:     testbench/vivado/2021.1/axis_dma_gray_adapter
//
// Referrences:     docs/02_interfaces.md
//
// Dependencies:    None
//
// Version:         V1.1
// Revision Date:   2026/10/27
// History:
// Time             Version     Revised by        Contents
// 2026/10/27       V1.1        maintainers       Add Gray8 framing and semantic documentation.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        material-counter 项目
// 开发人员:        WO-0014 维护者
//
// 创建日期:        2026年10月27日
// 设计名称:        Gray8 数据包适配器
// 模块名称:        axis_dma_gray_adapter
// 模块说明:        Description/axis_dma_gray_adapter_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/axis_dma_gray_adapter
//
// 参考资料:        docs/02_interfaces.md
//
// 依赖文件:        None
//
// 当前版本:        V1.1
// 修订日期:        2026年10月27日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年10月27日   V1.1        维护者            补充 Gray8 分包、字节序和反压说明。

module axis_dma_gray_adapter #(
	// 帧宽参数，单位为像素。
	parameter integer WIDTH = 320, // 每行 Gray8 像素数量。
	// 帧高参数，单位为像素。
	parameter integer HEIGHT = 240 // 帧内 Gray8 像素行数。
) (
	//-----------------全局信号-----------------//
	input wire aclk,                            // 所有同步状态的上升沿时钟。
	input wire aresetn,                         // 同步低有效复位，保持现有接口契约。

	//-----------------输入 AXI4-Stream 接口-----------------//
	input wire [31:0] s_tdata,                               // MM2S 当前 32 位数据字。
	input wire [3:0] s_tkeep,                                // MM2S 当前数据字的字节有效掩码。
	input wire s_tlast,                                      // MM2S 包尾标志，仅在 Footer 最后一个字有效。
	input wire s_tvalid,                                     // MM2S 当前数据字有效标志。
	output wire s_tready,                                    // 适配器可接收下一个 MM2S 数据字。

	//-----------------输出 Gray8 像素接口-----------------//
	output wire [7:0] m_tdata,                             // 当前输出的 Gray8 像素。
	output wire [0:0] m_tuser,                             // 第一像素的帧起始标志 SOF。
	output wire m_tlast,                                   // 每行最后一个像素的行结束标志 EOL。
	output wire m_tvalid,                                  // 当前输出 Gray8 像素有效标志。
	input wire m_tready,                                   // 下游接受当前 Gray8 像素的握手标志。

	//-----------------数据包状态接口-----------------//
	output reg packet_done,                           // Footer 校验通过并被接受时拉高一个周期。
	output reg packet_error,                          // 检测到格式错误的数据字时拉高一个周期。
	output reg [3:0] error_code,                      // 最近一次数据包格式错误的编码。
	output reg [31:0] frame_id                        // 从 Header 接受的帧编号。
);
	// Header/Footer 解析状态编码，确保数据包字段按序校验。
	localparam [3:0] HEADER_MAGIC = 4'd0;             // 等待 Header 起始 Magic。
	localparam [3:0] HEADER_GEOMETRY = 4'd1;          // 等待帧宽高字段。
	localparam [3:0] HEADER_LENGTH = 4'd2;            // 等待 Gray8 负载长度字段。
	localparam [3:0] HEADER_FRAME = 4'd3;             // 等待并锁存帧编号。
	localparam [3:0] PIXEL_PAYLOAD = 4'd4;            // 接收并串行输出 Gray8 负载。
	localparam [3:0] FOOTER_MAGIC = 4'd5;             // 校验 Footer 起始 Magic。
	localparam [3:0] FOOTER_FRAME = 4'd6;             // 校验 Footer 帧编号。
	localparam [3:0] FOOTER_CHECKSUM = 4'd7;          // 校验 Gray8 负载字节和。
	localparam [3:0] FOOTER_END = 4'd8;               // 校验 Footer 结束字与 TLAST。
	localparam [3:0] DROP_PACKET = 4'd9;              // 丢弃错误包直到 TLAST 恢复同步。

	// 帧像素数和数据包字段长度均以字节为单位计算。
	localparam [31:0] PIXEL_COUNT = WIDTH * HEIGHT;   // 单帧像素总数。
	localparam [31:0] PAYLOAD_BYTES = PIXEL_COUNT;    // Gray8 每像素一字节。
	localparam [31:0] EXPECTED_GEOMETRY = (HEIGHT << 16) | WIDTH; // Header 几何字段。
	localparam [2:0] TAIL_BYTES = (PAYLOAD_BYTES % 4 == 0) // 最后一个输入字的有效像素字节数。
		? 3'd4 : (PAYLOAD_BYTES % 4);
	localparam [31:0] FULL_WORD_BYTES = PAYLOAD_BYTES - TAIL_BYTES; // 完整四字节输入字覆盖范围。

	// 同步复位将这些流水状态清零；显式初值与复位后的空闲态一致。
	reg [3:0] packet_state = 0;                       // 当前 Header、Payload 或 Footer 解析状态。
	reg [3:0] next_packet_state = 0;                  // 组合逻辑计算的下一解析状态。
	reg [31:0] received_bytes = 0;                    // 当前帧已接收的 Gray8 负载字节数。
	reg [31:0] emitted_pixels = 0;                    // 当前帧已完成输出握手的像素数。
	reg [31:0] column_count = 0;                      // 当前像素所在行的列索引。
	reg [31:0] payload_checksum = 0;                  // 当前负载已累计的 32 位校验和。
	reg [31:0] gray_word_buffer = 0;                  // 保存尚未序列化完成的四像素输入字。
	reg [2:0] buffered_byte_count = 0;                // 当前缓冲字中有效像素字节数量。
	reg [2:0] buffered_byte_index = 0;                // 当前待输出像素的字节位置。
	reg gray_word_valid = 0;                          // 指示像素缓冲字仍有待输出内容。

	// 握手事件与字节计数决定输入、输出可并行推进的条件。
	wire output_fire;                                 // 输出像素在本周期完成一次有效握手。
	wire output_word_complete;                        // 当前缓冲数据字的最后一个像素被接受。
	wire input_fire;                                  // 输入数据字在本周期完成一次有效握手。
	wire [2:0] incoming_byte_count;                   // 当前输入负载字中有效像素字节数。
	wire [3:0] expected_keep;                         // 当前输入负载字要求的 TKEEP 掩码。
	wire [7:0] incoming_byte_1;                       // 首个高位 Lane 保存第二个串行像素。
	wire [7:0] incoming_byte_2;                       // 中间 Lane 解出第三个串行像素。
	wire [7:0] incoming_byte_3;                       // 最高八位段对应第四个串行像素。
	wire [8:0] incoming_sum_low;                      // 低半校验值覆盖像素字节 0 和 1。
	wire [8:0] incoming_sum_high;                     // 高半校验值覆盖像素字节 2 和 3。
	wire [9:0] incoming_checksum;                     // 当前输入数据字的 Gray8 校验和。
	wire packet_input_error;                          // 当前被接受的数据字存在格式错误。
	reg [3:0] packet_input_error_code = 0;            // 组合计算的当前输入错误编码。
	wire accepted_pixel_word;                         // 当前合法负载字可进入像素缓冲区。
	wire accepted_frame_header;                       // 帧编号字段完成合法握手。
	wire final_pixel_fire;                            // 当前帧最后一个像素完成输出握手。

	assign output_fire = gray_word_valid && m_tready; // 输出像素握手事件。
	assign output_word_complete = output_fire         // 当前缓冲字的最后像素已完成握手。
		&& buffered_byte_index == buffered_byte_count - 1'b1;
	assign input_fire = s_tvalid && s_tready;         // 输入数据字握手事件。
	assign incoming_byte_count = (received_bytes < FULL_WORD_BYTES) // 根据负载尾部决定当前字的像素数。
		? 3'd4 : TAIL_BYTES;
	assign expected_keep = (incoming_byte_count == 3'd4) ? 4'hf // 生成与有效 Gray8 字节数相符的 TKEEP。
		: (incoming_byte_count == 3'd3) ? 4'h7
		: (incoming_byte_count == 3'd2) ? 4'h3 : 4'h1;
	assign incoming_byte_1 = (incoming_byte_count >= 3'd2) // Lane 1 承载序列中的第二个 Gray8 像素。
		? s_tdata[15:8] : 8'd0;
	assign incoming_byte_2 = (incoming_byte_count >= 3'd3) // s_tdata[23:16] 解码为第三个像素字节。
		? s_tdata[23:16] : 8'd0;
	assign incoming_byte_3 = (incoming_byte_count >= 3'd4) // 四像素组有效时才消费最高字节 Lane 3。
		? s_tdata[31:24] : 8'd0;
	assign incoming_sum_low = {1'b0, s_tdata[7:0]}    // 合并当前字节对 0、1 形成低半校验值。
		+ {1'b0, incoming_byte_1};
	assign incoming_sum_high = {1'b0, incoming_byte_2} // 合并当前字节对 2、3 形成高半校验值。
		+ {1'b0, incoming_byte_3};
	assign incoming_checksum = {1'b0, incoming_sum_low} // 汇总四个 Gray8 像素字节的局部和。
		+ {1'b0, incoming_sum_high};
	assign packet_input_error = (packet_input_error_code != 4'd0); // 非零错误码表示当前输入字格式无效。
	assign accepted_pixel_word = input_fire && packet_state == PIXEL_PAYLOAD // 屏蔽格式错误并锁存合法负载字。
		&& !packet_input_error;
	assign accepted_frame_header = input_fire && packet_state == HEADER_FRAME // 只在合法帧号字握手后重置帧计数。
		&& !packet_input_error;
	assign final_pixel_fire = output_fire && emitted_pixels == PIXEL_COUNT - 1'b1; // 帧尾像素握手。

	// 当前字节序列器输出保持稳定，直到下游接受像素。
	assign s_tready = aresetn && ((packet_state != PIXEL_PAYLOAD) // 非负载状态总能接收控制字。
		|| (received_bytes < PAYLOAD_BYTES
			&& (!gray_word_valid || output_word_complete)));
	assign m_tvalid = gray_word_valid;                // 像素缓冲非空时声明输出有效。
	assign m_tdata = gray_word_buffer >> (buffered_byte_index * 8); // 逐字节移出当前 Gray8 像素。
	assign m_tuser = (emitted_pixels == 0);           // 首个帧像素携带 SOF。
	assign m_tlast = (column_count == WIDTH - 1);     // 每行末像素携带 EOL。

	// 根据当前解析状态检查 TKEEP、TLAST、Magic、几何尺寸、长度与校验字段。
	always @* begin
		packet_input_error_code = 4'd0;
		if (input_fire) begin
			case (packet_state)
				HEADER_MAGIC, HEADER_GEOMETRY, HEADER_LENGTH, HEADER_FRAME: begin
					if (s_tkeep != 4'hf) begin
						packet_input_error_code = 4'd3;
					end else if (s_tlast) begin
						packet_input_error_code = 4'd4;
					end else if (packet_state == HEADER_MAGIC && s_tdata != 32'h574f3134) begin
						packet_input_error_code = 4'd1;
					end else if (packet_state == HEADER_GEOMETRY && s_tdata != EXPECTED_GEOMETRY) begin
						packet_input_error_code = 4'd1;
					end else if (packet_state == HEADER_LENGTH && s_tdata != PAYLOAD_BYTES) begin
						packet_input_error_code = 4'd2;
					end
				end
				PIXEL_PAYLOAD: begin
					if (s_tkeep != expected_keep) begin
						packet_input_error_code = 4'd3;
					end else if (s_tlast) begin
						packet_input_error_code = 4'd4;
					end
				end
				FOOTER_MAGIC, FOOTER_FRAME, FOOTER_CHECKSUM, FOOTER_END: begin
					if (s_tkeep != 4'hf) begin
						packet_input_error_code = 4'd3;
					end else if (s_tlast != (packet_state == FOOTER_END)) begin
						packet_input_error_code = 4'd4;
					end else if ((packet_state == FOOTER_MAGIC && s_tdata != 32'h454e4434)
						|| (packet_state == FOOTER_FRAME && s_tdata != frame_id)
						|| (packet_state == FOOTER_CHECKSUM && s_tdata != payload_checksum)
						|| (packet_state == FOOTER_END && s_tdata != 32'hc0dec014)) begin
						packet_input_error_code = 4'd5;
					end
				end
				default: begin
					// 丢弃态不检查内容，只等待包尾重新同步。
				end
			endcase
		end
	end

	// 解析状态仅在同步复位或对应输入/输出握手事件后更新。
	always @* begin
		next_packet_state = packet_state;
		case (packet_state)
			HEADER_MAGIC: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: HEADER_GEOMETRY;
				end
			end
			HEADER_GEOMETRY: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: HEADER_LENGTH;
				end
			end
			HEADER_LENGTH: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: HEADER_FRAME;
				end
			end
			HEADER_FRAME: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: PIXEL_PAYLOAD;
				end
			end
			PIXEL_PAYLOAD: begin
				if (input_fire && packet_input_error) begin
					next_packet_state = s_tlast ? HEADER_MAGIC : DROP_PACKET;
				end else if (final_pixel_fire) begin
					next_packet_state = FOOTER_MAGIC;
				end
			end
			FOOTER_MAGIC: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: FOOTER_FRAME;
				end
			end
			FOOTER_FRAME: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: FOOTER_CHECKSUM;
				end
			end
			FOOTER_CHECKSUM: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: FOOTER_END;
				end
			end
			FOOTER_END: begin
				if (input_fire) begin
					next_packet_state = packet_input_error
						? (s_tlast ? HEADER_MAGIC : DROP_PACKET)
						: HEADER_MAGIC;
				end
			end
			DROP_PACKET: begin
				if (input_fire && s_tlast) begin
					next_packet_state = HEADER_MAGIC;
				end
			end
			default: begin
				next_packet_state = HEADER_MAGIC;
			end
		endcase
	end

	// 保持同步低有效复位契约，复位仅在时钟上升沿采样。
	always @(posedge aclk) begin
		if (!aresetn) begin
			packet_state <= HEADER_MAGIC;
		end else begin
			packet_state <= next_packet_state;
		end
	end

	// 接受 Gray8 负载、串行输出像素并汇总帧校验与状态脉冲。
	always @(posedge aclk) begin
		if (!aresetn) begin
			received_bytes <= 32'd0;
			emitted_pixels <= 32'd0;
			column_count <= 32'd0;
			payload_checksum <= 32'd0;
			gray_word_buffer <= 32'd0;
			buffered_byte_count <= 3'd0;
			buffered_byte_index <= 3'd0;
			gray_word_valid <= 1'b0;
			packet_done <= 1'b0;
			packet_error <= 1'b0;
			error_code <= 4'd0;
			frame_id <= 32'd0;
		end else begin
			packet_done <= 1'b0;
			packet_error <= 1'b0;

			if (output_fire) begin
				emitted_pixels <= emitted_pixels + 1'b1;
				if (column_count == WIDTH - 1) begin
					column_count <= 32'd0;
				end else begin
					column_count <= column_count + 1'b1;
				end

				if (output_word_complete) begin
					gray_word_valid <= 1'b0;
					buffered_byte_count <= 3'd0;
					buffered_byte_index <= 3'd0;
				end else begin
					buffered_byte_index <= buffered_byte_index + 1'b1;
				end
			end

			if (accepted_frame_header) begin
				frame_id <= s_tdata;
				received_bytes <= 32'd0;
				emitted_pixels <= 32'd0;
				column_count <= 32'd0;
				payload_checksum <= 32'd0;
				gray_word_valid <= 1'b0;
				buffered_byte_count <= 3'd0;
				buffered_byte_index <= 3'd0;
				error_code <= 4'd0;
			end

			if (accepted_pixel_word) begin
				received_bytes <= received_bytes + incoming_byte_count;
				payload_checksum <= payload_checksum + incoming_checksum;
				gray_word_buffer <= s_tdata;
				buffered_byte_count <= incoming_byte_count;
				buffered_byte_index <= 3'd0;
				gray_word_valid <= 1'b1;
			end

			if (input_fire && packet_input_error) begin
				packet_error <= 1'b1;
				error_code <= packet_input_error_code;
				gray_word_valid <= 1'b0;
				buffered_byte_count <= 3'd0;
				buffered_byte_index <= 3'd0;
			end

			if (input_fire && packet_state == FOOTER_END && !packet_input_error) begin
				packet_done <= 1'b1;
			end
		end
	end
endmodule
