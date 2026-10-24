# badge-screengrab

Grab the screen of an EMF Tildagon badge over USB serial, from a host such as
a Raspberry Pi. No app needs to be installed on the badge.

```
python3 grab.py                      # one screenshot -> grab_<timestamp>.png
python3 grab.py -o shot.png
python3 grab.py --gif out.gif -n 50 -d 0.2
python3 grab.py --live               # tkinter live window
```

Needs `pyserial`, `Pillow` and `numpy`. The badge is found by USB VID:PID
(`16d0:120e`); use `-p` to give a port.

It uses `display.get_framebuffer()` from the badge firmware (240x240 RGB565,
byte-swapped) through the raw REPL. USB serial output from the badge is only
about 10 KB/s, so a `@viper` encoder on the badge sends just the pixels that
changed since the previous grab, run-length coded. The first frame takes about
2 s and an unchanged screen about 0.4 s.

Notes: only one program can use the serial port at a time, and connecting
interrupts (Ctrl-C) whatever is running on the badge. To trigger a grab from
the badge itself, see
[tildagon-screengrab](https://github.com/Corteil/tildagon-screengrab).

![example](example.png)

License: MIT
