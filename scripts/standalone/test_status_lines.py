import copy

from scripts.standalone.integrated_run import build_status_lines


PAGE_STATIC = ["640x400 / RLE 50MHz", "显示 1280x720 60Hz",
               "LUT 24.47% / FF 16.55%", "BRAM 3.21% / DSP 4.09%"]


def _telemetry(stale=(), monotonic=1.0):
    return {
        "resources": {
            "value": {
                "memory": {"CmaFree": "1234 kB", "MemAvailable": "200000 kB"},
                "cpu": {"cpu0": 90, "cpu1": 80, "system": 85},
                "processes": {
                    "count_capture_video": {"cpu_percent": 100.4},
                    "drawing": {"cpu_percent": 50.2},
                },
                "sd_free_bytes": 2 * 1024 ** 3,
            },
            "stale": "resources" in stale,
            "sampled_monotonic": monotonic,
        },
        "xadc": {
            "value": {"channels": [
                {"channel": "in_temp", "label": "temp", "value_si": 45.6},
                {"channel": "vccint_raw", "label": "vccint", "value_si": 1.0},
                {"channel": "vccaux_raw", "label": "vccaux", "value_si": 1.8},
                {"channel": "vccbram_raw", "label": "vccbram", "value_si": 1.0},
            ]},
            "stale": "xadc" in stale,
            "sampled_monotonic": monotonic,
        },
        "performance": {
            "value": {"processing_fps": 12.3, "runs": 500, "rx_bytes": 4000},
            "stale": "performance" in stale,
            "sampled_monotonic": monotonic,
        },
    }


def test_status_lines_without_page_and_without_telemetry_lines():
    lines = build_status_lines(5, False, False, False, True,
                               _telemetry(), None, PAGE_STATIC)
    assert lines == ["本次会话累计 5", "运行中", "重启清零 / 过线诊断留图"]


def test_status_lines_include_fps_stale_and_annotation_notes():
    telemetry = _telemetry(stale=("resources",))
    performance = telemetry["performance"]
    lines = build_status_lines(7, True, False, True, False,
                               telemetry, performance, PAGE_STATIC)
    assert lines[:6] == [
        "本次会话累计 7",
        "暂停计数",
        "重启清零 / 诊断留图关闭",
        "标注仅显示前128项 / 计数未截断",
        "处理 12.3 FPS",
        "过期: resources",
    ]


def test_status_lines_page_contains_static_and_dynamic_values():
    telemetry = _telemetry()
    lines = build_status_lines(9, False, True, False, True,
                               telemetry, telemetry["performance"], PAGE_STATIC)
    assert lines[:4] == ["本次会话累计 9", "运行中", "重启清零 / 过线诊断留图",
                         "处理 12.3 FPS"]
    assert lines[4:8] == PAGE_STATIC
    assert lines[8:] == [
        "CMA 1234 kB",
        "CPU0 90% / CPU1 80% / 系统 85%",
        "进程 计数 100% / 绘图 50%",
        "内存 195 MB / SD 2.0 GB",
        "温度 45.6 °C",
        "INT 1.000 / AUX 1.800 / BRAM 1.000 V",
        "RLE 500 runs / DMA 4000 B",
    ]


def test_status_lines_do_not_mutate_inputs():
    telemetry = _telemetry()
    before = copy.deepcopy(telemetry)
    build_status_lines(1, False, True, False, True,
                       telemetry, telemetry["performance"], PAGE_STATIC)
    assert telemetry == before