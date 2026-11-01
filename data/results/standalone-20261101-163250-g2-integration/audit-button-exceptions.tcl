set_param general.maxThreads 1
open_project {Q:/data/results/standalone-20261101-163250-g2-integration/project/display.xpr}
open_run impl_1
set pins [get_pins -quiet -hier -regexp {.*button_debounce.*button_meta_reg\[[0-3]\]/D}]
puts "OFFLOAD_BUTTON_META_D_PIN_COUNT=[llength $pins]"
foreach pin $pins {puts "OFFLOAD_BUTTON_META_D_PIN=[get_property NAME $pin]"}
report_exceptions -coverage -file {Q:/data/results/standalone-20261101-163250-g2-integration/evidence/button-exception-coverage.rpt}
if {[llength $pins] != 4} {puts stderr "OFFLOAD_BUTTON_META_D_PIN_COUNT_MISMATCH:[llength $pins]"; exit 2}
puts "OFFLOAD_BUTTON_META_NETLIST_AUDIT_PASS"
exit 0