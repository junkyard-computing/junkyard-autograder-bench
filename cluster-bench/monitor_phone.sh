watch -n 1 '
  echo "======= PHONE MONITOR ======="
  echo "GPU"
  echo "  utilization : % of GPU busy           $(cat /sys/class/misc/mali0/device/utilization) %"
  echo "  frequency   : current clock speed     $(( $(cat /sys/class/misc/mali0/device/cur_freq) /
  1000 )) MHz"
  echo ""
  echo "CPU (8 cores: 1x X1 BIG, 3x A76 MID, 4x A55 LITTLE)"
  echo "  load 1m     : avg runnable threads    $(awk "{print \$1}" /proc/loadavg)  (8.0 = all
  cores full)"
  echo "  load 5m     :                         $(awk "{print \$2}" /proc/loadavg)"
  echo "  load 15m    :                         $(awk "{print \$3}" /proc/loadavg)"
  echo "  threads     : running / total         $(awk "{print \$4}" /proc/loadavg)"
  echo ""
  echo "TEMPERATURE (C)"
  echo "  BIG         : Cortex-X1 cluster       $(( $(cat /sys/class/thermal/thermal_zone0/temp) /
  1000 )) C"
  echo "  MID         : Cortex-A76 cluster      $(( $(cat /sys/class/thermal/thermal_zone1/temp) /
  1000 )) C"
  echo "  LITTLE      : Cortex-A55 cluster      $(( $(cat /sys/class/thermal/thermal_zone2/temp) /
  1000 )) C"
  echo "  G3D         : GPU                     $(( $(cat /sys/class/thermal/thermal_zone3/temp) /
  1000 )) C"
  echo "  skin        : outer surface           $(( $(cat /sys/class/thermal/thermal_zone9/temp) /
  1000 )) C"
  echo "============================="
  '
<< 'COMMENT'
#this one gives more info but less anotation. Harder to understand. Use if more data than above is needed
watch -n 1 '
    echo "=== GPU ===";
    echo "Util:    $(cat /sys/class/misc/mali0/device/utilization)";
    echo "Freq:    $(cat /sys/class/misc/mali0/device/cur_freq)";
    echo "=== CPU ===";
    cat /proc/loadavg;
    echo "=== Temps ===";
    for z in /sys/class/thermal/thermal_zone*; do
      echo "$(cat $z/type): $(cat $z/temp)";
    done
  '
COMMENT
