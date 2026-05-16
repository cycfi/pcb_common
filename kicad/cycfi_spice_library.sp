* Cycfi KiCad simulation models.

* Generic open-collector comparator.
* Pins: in+ in- vcc vee out
* OUT is high impedance when V(in+) > V(in-), and pulls to VEE when
* V(in-) > V(in+). Use an external pull-up resistor on OUT.
.subckt cycfi_open_collector_comparator in+ in- vcc vee out params: VOS=0 RON=10 ROFF=1e12
  BOUT out vee I=if(V(in-,in+)+{VOS}>0,V(out,vee)/{RON},V(out,vee)/{ROFF})
.ends cycfi_open_collector_comparator

* ADG779 SPDT analog switch.
* Pins: in vdd gnd s1 d s2
* Truth table: IN=0 connects D-S1; IN=1 connects D-S2.
.subckt cycfi_adg779_spdt in vdd gnd s1 d s2 params: RON=2.5 ROFF=1e12 VTH=0.5
  BDS1 d s1 I=if(V(in,gnd)<(V(vdd,gnd)*{VTH}), V(d,s1)/{RON}, V(d,s1)/{ROFF})
  BDS2 d s2 I=if(V(in,gnd)>=(V(vdd,gnd)*{VTH}), V(d,s2)/{RON}, V(d,s2)/{ROFF})
  RIN in gnd 1G
.ends cycfi_adg779_spdt

* CMOS open-drain inverter.
* Pins: in out vcc gnd
* OUT is high impedance when IN is logic-low, and pulls to GND when
* IN is logic-high. Use an external pull-up resistor on OUT.
.subckt cycfi_open_drain_inverter in out vcc gnd params: RON=10 ROFF=1e12 VTH=0.5
  BOUT out gnd I=if(V(in,gnd)>=(V(vcc,gnd)*{VTH}), V(out,gnd)/{RON}, V(out,gnd)/{ROFF})
  RIN in gnd 1G
.ends cycfi_open_drain_inverter
