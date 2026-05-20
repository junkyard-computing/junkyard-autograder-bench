#this is meant to be run on a phone e.g. PF-027
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
