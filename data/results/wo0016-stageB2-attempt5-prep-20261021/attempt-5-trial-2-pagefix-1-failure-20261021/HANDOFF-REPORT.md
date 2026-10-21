# Attempt 5, trial 2 control-page handoff failure

- Current Boot ID: d9f65950-d59b-4ea4-bee4-b28656cb176f.
- Attempt ID: attempt-5-trial-2-pagefix-1.
- Permit SHA-256: 94b30a1b89d4dd67bd3ae705c8c37dd66cdfd11a6fa0a18846e59a1a44435523.
- Consumed marker SHA-256: b902e5e639c26cfaccc9d27347ba95c079f87001f267502e6d624899e124bfb5.
- Result: BOARD_STREAM_FAILED; frames_processed=0; frame_0_start_monotonic=null; no camera open; no PYNQ import; no board access; no Overlay initialization; no physical sample.
- Error: ModuleNotFoundError: No module named 'pynq'. The systemd command used /usr/bin/python3 from the deployment working directory. Board PYNQ files are under /usr/local/share/pynq-venv; its interpreter is /usr/local/share/pynq-venv/bin/python3. A future authorized launch must use that runtime.
- A preceding launcher-only command-line parse failure did not create a consumed marker. Its journal is preserved separately.
- Final read-only state: Stage 1 is OVERLAY_LOAD_PASS; FPGA Manager operating / flags=0; READY Guard remains consumed=false; operator service inactive; 8080/8081/9090 have no listeners; /dev/video0 is free; CmaFree=79,268 KiB.
- The one cache-clear authorization allocated for this intended physical round has been used. Current CmaFree is below the prior 110,000 KiB startup target, so another controlled clear and a new unique one-shot permit require explicit authorization.

Evidence:
- Consumed attempt directory archive SHA-256: fc6d128ea90f526c4f51376622c51c05b5805fb9366121b0f37624830eca23cc.
- Launcher argument-parse failure journal SHA-256: c9bc9b6c17fa2e191cdad4fe3c6379da32aa708ac01547f4abe7a6753f7da941.