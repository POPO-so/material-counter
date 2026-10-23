"""Synchronize closeout prose with the explicit test log; no evidence reanalysis."""
import hashlib
from pathlib import Path
import re

OUT = Path(__file__).resolve().parents[2]
log = (OUT / 'new-targeted-tests.txt').read_text(encoding='utf-8-sig')
match = re.search(r'(\d+) passed, (\d+) subtests passed', log)
assert match
methods, subtests = map(int, match.groups())
assert (methods, subtests) == (50, 20)
total = methods + subtests
path = OUT / 'verification-report.md'
text = path.read_text(encoding='utf-8')
text = text.replace('**44 passed + 11 subtests passed**', f'**{methods} passed + {subtests} subtests passed**')
text = text.replace('JUnit 共 55 条记录包含子测试，不能称为 55 个独立测试方法', f'JUnit 共 {total} 条记录包含子测试，不能称为 {total} 个独立测试方法')
text = text.replace('预案 21 方法+11 子测试', '预案 27 方法+20 子测试')
text = text.replace('汇总测试最初误用整个结果目录', '前轮汇总测试曾误用整个结果目录')
text = text.replace('不能声称整个会话“绝无镜像来源 Python 语句执行”', '不能声称前轮整个会话“绝无镜像来源 Python 语句执行”')
if '## POSIX 增量封板记录' not in text:
    text += f'\n## POSIX 增量封板记录\n\n本次三项边界已在共享核心、冻结文本、mock 回归及两份 v2 文档中同步闭环。核心与冻结脚本均继续 NOT_APPROVED_DO_NOT_EXECUTE；所有测试只注入 fake OS，没有执行冻结入口或真实捕获后端。预案定向结果为27方法+20子测试；显式四文件汇总为{methods}方法+{subtests}子测试，不与前轮44+11或历史459/76/43相加。\n\n新增回归覆盖非普通文件FIFO/字符设备/块设备/目录/socket、size为零/超过32MiB、dir/file跨设备、B1 root零文件打开、proc超过64KiB以及root/boot设备相同和不同的元数据。全部B2读取前失败均校验零内容读取与双fd关闭。以已有decoded-tables.json检查替代两项实镜像表重解，仍保留合成decoder和小DTB测试。本次没有重哈希大镜像或重解21张真实FSBL表，也没有发生目录式pytest收集。\n\n前轮汇总、索引及审查记录保留于manager/posix-closeout/previous-*；POSIX红绿日志位于proposals/logs/posix-*。当前实现/模板SHA与完整交付清单由最终索引重新封存。继续等待用户决定保持离线，或另行审阅并单独审批B1；本次不请求也不推定板端授权。\n'
path.write_text(text, encoding='utf-8')
path = OUT / 'review-brief-v2.md'
text = path.read_text(encoding='utf-8')
text = text.replace('44 个方法通过 + 11 个子测试通过；JUnit 55 条记录', f'{methods} 个方法通过 + {subtests} 个子测试通过；JUnit {total} 条记录')
text = text.replace('预案21方法+11子测试', '预案27方法+20子测试')
text = text.replace('曾有一次汇总测试误收集', '前轮曾有一次汇总测试误收集')
if '## POSIX 封板增量结论' not in text:
    text += f'\n## POSIX 封板增量结论\n\n本次共享核心、冻结文本与白名单已同步完成三项POSIX加固：O_NONBLOCK及普通文件/正32MiB上限门，dir/file同st_dev及boot_dev_differs_from_root元数据，B1最前置非root门与统一64KiB受限helper。对应新边界mock全部通过；当前四文件汇总{methods}+{subtests}子测试，不与前轮44+11相加。实镜像21表测试改核对封存JSON；本次没有读取/哈希8.33GB镜像或重解21张表，没有目录式测试收集。\n\n状态保持STOPPED_PRECONDITION_UNVERIFIED和NOT_APPROVED_DO_NOT_EXECUTE；无板端接触、无SSH、无B1/B2执行。请后续由用户决定继续离线，或单独审批B1；本轮不自动推进。\n'
path.write_text(text, encoding='utf-8')
path = OUT / 'command-audit-and-whitelist-v2.md'
text = path.read_text(encoding='utf-8').replace('required injected OS/read dependencies', 'a required injected OS dependency')
path.write_text(text, encoding='utf-8')
path = OUT / 'proposals/findings.md'
text = path.read_text(encoding='utf-8')
text = text.replace('Final `logs/task-d-unit-tests.xml`:', 'Prior-stage `logs/task-d-unit-tests.xml`:')
digest = hashlib.sha256((OUT / 'proposals/remote_template_v2.py').read_bytes()).hexdigest()
text = re.sub(r'(`remote-template-sha256.txt`: `)[a-f0-9]{64}(`)', r'\g<1>' + digest + r'\2', text)
if '## POSIX closeout' not in text:
    text += f'\n## POSIX closeout\n\nThe current core has no custom reader entry: capture_b1(*, os) checks effective UID first and uses read_b1_file for all six paths and both boot checks. MAX_PROC_READ_BYTES=65536; overflow fails. capture_b2(*, os) uses nonblocking openat, a positive 32MiB size bound, S_ISREG and ST_RDONLY before any content, pinned directory/file device equality, and metadata-only root stat. boot_dev_differs_from_root is in CaptureResult and the frozen B2 JSON payload. Precondition failures close both fds without content reads.\n\nCurrent proposal suite: 27 methods and 20 subtests; four-file suite: {methods} methods and {subtests} subtests. Red/green evidence is in logs/posix-red.txt and logs/posix-green.txt. Frozen texts are statically parsed/hashed only and never executed. Source SHA-256: `{digest}`. Prior log counts above are historical stage evidence.\n'
path.write_text(text, encoding='utf-8')
print(f'Synchronized POSIX reports: {methods} methods + {subtests} subtests; no large inputs read.')

