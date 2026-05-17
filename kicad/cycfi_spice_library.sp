* Cycfi KiCad simulation models.

* Generic open-collector comparator.
* Pins: in+ in- vcc vee out
* OUT is high impedance when V(in+) > V(in-), and pulls to VEE when
* V(in-) > V(in+). Use an external pull-up resistor on OUT.
* VSW smooths the transition for convergence.
.subckt cycfi_open_collector_comparator in+ in- vcc vee out params: VOS=0 RON=10 ROFF=1e12 VSW=2m VPWR=0.5 VSWP=50m
  BOUT out vee I=V(out,vee)*((0.5*(1+tanh((V(vcc,vee)-{VPWR})/{VSWP})))*((0.5*(1+tanh((V(in-,in+)+{VOS})/{VSW})))/{RON}+(1-(0.5*(1+tanh((V(in-,in+)+{VOS})/{VSW}))))/{ROFF})+(1-(0.5*(1+tanh((V(vcc,vee)-{VPWR})/{VSWP}))))/{ROFF})
.ends cycfi_open_collector_comparator

* ADG779 SPDT analog switch.
* Pins: in vdd gnd s1 d s2
* Truth table: IN=0 connects D-S1; IN=1 connects D-S2.
* VSW smooths the transition for convergence when the switch is in feedback.
.subckt cycfi_adg779_spdt in vdd gnd s1 d s2 params: RON=2.5 ROFF=1e12 VTH=0.5 VSW=10m VPWR=0.5 VSWP=50m
  BDS1 d s1 I=V(d,s1)*((0.5*(1+tanh((V(vdd,gnd)-{VPWR})/{VSWP})))*((1-(0.5*(1+tanh((V(in,gnd)-V(vdd,gnd)*{VTH})/{VSW}))))/{RON}+(0.5*(1+tanh((V(in,gnd)-V(vdd,gnd)*{VTH})/{VSW})))/{ROFF})+(1-(0.5*(1+tanh((V(vdd,gnd)-{VPWR})/{VSWP}))))/{ROFF})
  BDS2 d s2 I=V(d,s2)*((0.5*(1+tanh((V(vdd,gnd)-{VPWR})/{VSWP})))*((0.5*(1+tanh((V(in,gnd)-V(vdd,gnd)*{VTH})/{VSW})))/{RON}+(1-(0.5*(1+tanh((V(in,gnd)-V(vdd,gnd)*{VTH})/{VSW}))))/{ROFF})+(1-(0.5*(1+tanh((V(vdd,gnd)-{VPWR})/{VSWP}))))/{ROFF})
  RIN in gnd 1G
.ends cycfi_adg779_spdt

* CMOS open-drain inverter.
* Pins: input output vcc gnd
* OUT is high impedance when IN is logic-low, and pulls to GND when
* IN is logic-high. Use an external pull-up resistor on OUT.
.subckt cycfi_open_drain_inverter input output vcc gnd params: RON=10 ROFF=1e12 VTH=0.5 VSW=10m VPWR=0.5 VSWP=50m
  BOUT output gnd I=V(output,gnd)*((0.5*(1+tanh((V(vcc,gnd)-{VPWR})/{VSWP})))*((0.5*(1+tanh((V(input,gnd)-V(vcc,gnd)*{VTH})/{VSW})))/{RON}+(1-(0.5*(1+tanh((V(input,gnd)-V(vcc,gnd)*{VTH})/{VSW}))))/{ROFF})+(1-(0.5*(1+tanh((V(vcc,gnd)-{VPWR})/{VSWP}))))/{ROFF})
  RIN input gnd 1G
.ends cycfi_open_drain_inverter

* Generic non-inverting Schmitt trigger.
* Pins: in vcc vee out
* VTH and VH are fractions of V(vcc,vee). Defaults: rising=0.6*VCC, falling=0.4*VCC.
.subckt cycfi_schmitt_trigger in vcc vee out params: VTH=0.5 VH=0.2 ROUT=10 RSTATE=1k CSTATE=1n
  BSTATE state vee I=if(V(in,vee)>(({VTH}+{VH}/2)*V(vcc,vee)),(V(vcc,vee)-V(state,vee))/{RSTATE},if(V(in,vee)<(({VTH}-{VH}/2)*V(vcc,vee)),(0-V(state,vee))/{RSTATE},0))
  CSTATE state vee {CSTATE} ic=0
  RLEAK state vee 1G
  BDRV out_drv vee V=limit(V(state,vee),0,V(vcc,vee))
  ROUT out_drv out {ROUT}
.ends cycfi_schmitt_trigger
