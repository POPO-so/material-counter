`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:         material-counter project
// Engineer:        WO-0014 maintainers
//
// Create Date:     2026/10/27
// Design Name:     Gray8 to Binary8 image pipeline
// Module Name:     axis_dma_image_pipeline
// Description:     description/axis_dma_image_pipeline_Design.pdf
// Simulations:     testbench/vivado/2021.1/axis_dma_image_pipeline
//
// Referrences:     docs/02_interfaces.md
//
// Dependencies:    axis_dma_gray_adapter, axis_gray2binary, axis_binary_output_checker
//
// Version:         V1.1
// Revision Date:   2026/10/27
// History:
// Time             Version     Revised by        Contents
// 2026/10/27       V1.1        maintainers       Connect native Gray8 input to Binary8 thresholding.
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:        material-counter 项目
// 开发人员:        WO-0014 维护者
//
// 创建日期:        2026年10月27日
// 设计名称:        Gray8 到 Binary8 图像流水线
// 模块名称:        axis_dma_image_pipeline
// 模块说明:        Description/axis_dma_image_pipeline_Design.pdf
// 仿真工程:        TestBench/Vivado/2021.1/axis_dma_image_pipeline
//
// 参考资料:        docs/02_interfaces.md
//
// 依赖文件:        axis_dma_gray_adapter.v、axis_gray2binary.v、axis_binary_output_checker.v
//
// 当前版本:        V1.1
// 修订日期:        2026年10月27日
// 修订历史:
// 时间             版本        修订人            修订内容
// 2026年10月27日   V1.1        维护者            灰度像素直接进入阈值模块，不经过 RGB2Gray。

module axis_dma_image_pipeline #(
	// 输入帧宽，单位为像素。
	parameter integer WIDTH = 320, // 每行输入的 Gray8 像素数。
	// 输入帧高，单位为像素。
	parameter integer HEIGHT = 240, // 输入图像包含的像素行数。
	// Gray8 到 Binary8 的固定阈值。
	parameter integer THRESHOLD = 128 // 像素大于阈值时输出白色。
) (
	//-----------------全局信号-----------------//
	input wire aclk,                            // 全链路同步采样时钟。
	input wire aresetn,                         // 全链路同步低有效复位。

	//-----------------输入 AXI4-Stream 接口-----------------//
	input wire [31:0] s_tdata,                               // 32 位 MM2S 包数据字。
	input wire [3:0] s_tkeep,                                // MM2S 每个数据字的有效字节掩码。
	input wire s_tlast,                                      // MM2S 包尾标志，仅用于 Footer 校验。
	input wire s_tvalid,                                     // MM2S 输入数据有效标志。
	output wire s_tready,                                    // 上游可继续发送数据字。

	//-----------------输出 Binary8 AXI4-Stream 接口-----------------//
	output wire [7:0] m_tdata,                                       // 当前阈值化后的 Binary8 像素。
	output wire [0:0] m_tuser,                                       // 第一像素的 SOF 标志。
	output wire m_tlast,                                             // 每行最后像素的 EOL 标志。
	output wire m_tvalid,                                            // 当前二值像素有效标志。
	input wire m_tready,                                             // 下游接受当前二值像素的握手标志。

	//-----------------数据包与输出检查状态接口-----------------//
	output wire packet_done,                                    // 输入包 Footer 校验通过状态。
	output wire packet_error,                                   // 输入包格式错误状态。
	output wire [3:0] error_code,                               // 输入包格式错误编码。
	output wire [31:0] frame_id,                                // 已接受的输入帧编号。
	output wire checker_error,                                  // 输出帧像素数或标志错误状态。
	output wire [31:0] observed_pixels,                         // 已握手的输出像素总数。
	output wire [31:0] observed_sofs,                           // 累计已接受的帧首标志数量。
	output wire [31:0] observed_eols,                           // 累计已接受的行尾标志数量。
	output wire [31:0] observed_checksum                        // 已握手输出像素字节和。
);
	// Gray8 解析器到阈值模块间的逐像素数据与帧标志。
	wire [7:0] gray_data;                                       // 当前解析出的 Gray8 像素。
	wire [0:0] gray_sof;                                        // Gray8 帧起始标志。
	wire gray_eol;                                              // Gray8 行结束标志。
	wire gray_valid;                                            // 当前 Gray8 像素有效标志。
	wire gray_ready;                                            // 阈值模块可接收 Gray8 像素。

	// 解析 Header/Footer 并按 byte 0 到 byte 3 顺序输出 Gray8。
	axis_dma_gray_adapter #(                                    // Gray8 包字段解析器和像素拆包实例。
		.WIDTH(WIDTH),                                          // 使用统一配置的帧宽。
		.HEIGHT(HEIGHT)                                         // 使用统一配置的帧高。
	)

	// 将包字段解析结果送往单通道像素处理通路。
	packet_adapter (
		.aclk(aclk),                                            // 为包解析状态机提供采样边沿。
		.aresetn(aresetn),                                      // 清空解析寄存器并等待新 Header。
		.s_tdata(s_tdata),                                      // 输入 32 位 Gray8 打包字。
		.s_tkeep(s_tkeep),                                      // 输入字节有效掩码。
		.s_tlast(s_tlast),                                      // 输入数据包尾标志。
		.s_tvalid(s_tvalid),                                    // 仅在上游声明数据字有效时推进解析。
		.s_tready(s_tready),                                    // 返回输入反压状态。
		.m_tdata(gray_data),                                    // 输出解析后的 Gray8 像素。
		.m_tuser(gray_sof),                                     // 输出 Gray8 帧起始标志。
		.m_tlast(gray_eol),                                     // 输出 Gray8 行结束标志。
		.m_tvalid(gray_valid),                                  // 输出 Gray8 像素有效标志。
		.m_tready(gray_ready),                                  // 接收阈值模块的 ready 反压。
		.packet_done(packet_done),                              // 输出包尾校验通过脉冲。
		.packet_error(packet_error),                            // 输出数据包错误脉冲。
		.error_code(error_code),                                // 输出最近一次错误编码。
		.frame_id(frame_id)                                     // 输出当前接受的帧编号。
	);

	// 将 Gray8 像素直接阈值化为 Binary8，不经过 RGB2Gray。
	axis_gray2binary #(                                         // Gray8 阈值化流水实例。
		.THRESHOLD(THRESHOLD)                                   // 绑定当前流水线阈值参数。
	)

	// 对每个解析像素直接执行 Gray8 阈值比较。
	gray_to_binary (
		.aclk(aclk),                                            // 驱动像素阈值流水寄存器。
		.aresetn(aresetn),                                      // 清除阈值输出与伴随帧标记。
		.s_tdata(gray_data),                                    // 输入解析后的 Gray8 像素。
		.s_tuser(gray_sof),                                     // 输入 Gray8 帧起始标志。
		.s_tlast(gray_eol),                                     // 输入 Gray8 行结束标志。
		.s_tvalid(gray_valid),                                  // 输入 Gray8 像素有效标志。
		.s_tready(gray_ready),                                  // 返回阈值模块 ready 状态。
		.m_tdata(m_tdata),                                      // 输出阈值化 Binary8 像素。
		.m_tuser(m_tuser),                                      // 传递 Binary8 帧起始标志。
		.m_tlast(m_tlast),                                      // 传递 Binary8 行结束标志。
		.m_tvalid(m_tvalid),                                    // 标记寄存器中的二值像素可以被读取。
		.m_tready(m_tready)                                     // 接收 S2MM 下游反压。
	);

	// 仅观察已完成的 Binary8 输出握手，不进入 ready 反压路径。
	axis_binary_output_checker #(                               // 观测 Binary8 输出帧协议的独立检查实例。
		.WIDTH(WIDTH),                                          // 按配置帧宽核验输出行边界。
		.HEIGHT(HEIGHT)                                         // 按配置帧高核验总像素数。
	)

	// 独立统计完成的输出握手，不改变 ready 通路。
	output_checker (
		.aclk(aclk),                                            // 让观察计数器与输出握手同沿更新。
		.aresetn(aresetn),                                      // 清零统计值和上一帧边界记录。
		.s_tdata(m_tdata),                                      // 观察 S2MM 前的 Binary8 像素。
		.s_tuser(m_tuser),                                      // 观察 S2MM 前的 SOF 标志。
		.s_tlast(m_tlast),                                      // 交给行尾检测器核验每行边界。
		.s_tvalid(m_tvalid),                                    // 仅统计阈值模块声明有效的数据。
		.s_tready(m_tready),                                    // 观察输出接受握手状态。
		.checker_error(checker_error),                          // 输出帧格式核验错误。
		.observed_pixels(observed_pixels),                      // 输出完成握手的像素计数。
		.observed_sofs(observed_sofs),                          // 汇总输入观察点接受的帧首标记。
		.observed_eols(observed_eols),                          // 汇总输入观察点接受的行尾标记。
		.observed_checksum(observed_checksum)                   // 输出 Binary8 字节累加值。
	);
endmodule
