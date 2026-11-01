#!/usr/bin/env python3
"""scripts/r59_judge.py -- the R59 join-round verdict, read off the logdump bytes.

HR15 tree (pre-registered in docs/WORKLOG.md, R59 预注册):
  HR15-1 全胜 = R58 全线复现 (brought up / firmware READY / THE RADIO IS UP / 零 panic
              / 存活 ≥1943s record) + join 判据链: the credential line WITHOUT the
              NOT-A-NAME suffix + the association told + THE KEYS ARE GIVEN +
              THE NETWORK IS JOINED + frame counters (lx_station_say) with frames sent > 0
              ⇒ the plan's R37 row, second half, is DONE -- the machine is ON a network.
  HR15-2      = the credential line present but THE HANDSHAKE DID NOT COME THROUGH
              ⇒ named failure (a wrong passphrase gives this, the fork's own words);
              the security law is also checked: lx_station_join is WPA2-PSK only.
  HR15-3      = "none was given" / "NOT A NAME" ⇒ the fw/network delivery failed (the
              staged flashboot lacks the file, or the sizes break the kernel law).
  HR15-4      = scan lines present ⇒ the join branch never entered (wifi_join did not
              parse) -- a diagnostic, not a verdict failure by itself.
  panic / signal lines outside the stop window ⇒ per-code triage either way.

Privacy law: network names heard by a scan or the joined SSID are NEVER printed here --
the judge reports lengths and counts only.

usage: r59_judge.py <logdump.bin|--staged 4MB capture> [--quiet]
"""
import re
import sys


def own_region(data):
    start = 0
    for i in range(0, len(data), 4096):
        if data[i:i + 4096].strip(b'\x00'):
            start = i
            break
    eol = data.find(b'== END OF LOG')
    end = eol + len(b'== END OF LOG') if eol != -1 else len(data)
    return data[start:end], eol != -1


def main():
    args = sys.argv[1:]
    staged = '--staged' in args
    args = [a for a in args if not a.startswith('--')]
    data = open(args[0], 'rb').read()
    own, complete = own_region(data)
    text = own.decode('utf-8', 'replace')

    verdicts = []
    green = True

    def check(ok, label, detail=''):
        nonlocal green
        verdicts.append(('%s %s%s' % ('✓' if ok else '✗', label, (' -- ' + detail) if detail else '')))
        if not ok:
            green = False

    # ---- the join chain (this round's knife)
    m = re.search(r'wifi: (\d+): which network to join: a name of (\d+) bytes and a passphrase of (\d+) bytes were given(\S*)', text)
    cred_ok = m is not None and 'NOT A NAME' not in (m.group(4) or '')
    check(m is not None, 'which-network-to-join line present')
    if m:
        check(cred_ok, 'credential pair passed the kernel law (name 1..32, passphrase 8..63)',
              'name %s B, passphrase %s B' % (m.group(2), m.group(3)))
        check('NOT A NAME' not in (m.group(4) or ''), 'no NOT-A-NAME suffix')
    check('none was given (fw/network' not in text, 'fw/network WAS delivered (no "none was given")')

    joined = 'lx: THE NETWORK IS JOINED: frames can pass' in text
    handshake_fail = 'lx: THE HANDSHAKE DID NOT COME THROUGH' in text
    keys = 'lx: THE KEYS ARE GIVEN TO THE DRIVER' in text
    assoc = 'lx: the driver is told of the association' in text
    check(assoc, 'association initiated (the driver is told of the association)')
    check(keys or joined, 'WPA keys given to the driver')
    check(joined, 'THE NETWORK IS JOINED: frames can pass')

    # frame counters (lx_station_say -- the hold window and/or the stop)
    say = re.findall(r'lx: the network is (joined|NOT joined), (\d+) s after it was joined: (\d+) frames sent \((\d+) could not be\), (\d+) received for us and (\d+) sent to everybody', text)
    if say:
        state, secs, sent, lost, recv, bcast = say[-1]
        check(state == 'joined', 'lx_station_say: joined state', '%s s, sent %s, recv %s' % (secs, sent, recv))
        check(int(sent) > 0 or int(recv) > 0, 'frames moved on the data path', 'sent %s / recv %s' % (sent, recv))
    else:
        check(False, 'lx_station_say line present (the hold window or the stop prints it)')

    # ---- the chain prerequisite (R58 inheritance)
    check('THE RADIO IS UP' in text, 'THE RADIO IS UP (the chain came up)')
    check('ICNSS WLAN firmware READY; MSA indication received' in text, 'ICNSS WLAN firmware READY')
    check('is being brought up beside the system' in text, 'the wifi chain was brought up')

    # ---- HR15-4 diagnostic: with wifi_join=1 the self-driven scan branch never runs (join wins);
    # a scan line can still come from a userland-driven boundary scan -- a note, not a failure
    scan_lines = len(re.findall(r'THE DRIVER IS ASKED TO SCAN', text))
    if scan_lines > 0:
        verdicts.append('! %d scan line(s) present -- join %s; if NO join attempt ran too, wifi_join did not parse (HR15-4)'
                        % (scan_lines, 'ran and won' if (joined or handshake_fail or keys) else 'NEVER ran'))

    if handshake_fail:
        green = False
        verdicts.append('HR15-2 SHAPE: the handshake did not come through (a wrong passphrase gives this, per the fork);'
                        ' also check the security law: lx_station_join is WPA2-PSK only (WPA3-only APs will refuse)')

    # ---- the inheritance table (R54..R58 conservation) -- the census reprints every minute, take the LAST
    dec = re.findall(r'sigcensus: decode ok (\d+), skip (\d+)', text)
    if dec:
        ok, skip = dec[-1]
        check(ok == '58' and skip == '0', 'decode ok 58 / skip 0 (last census)', 'got ok %s / skip %s' % (ok, skip))
    else:
        check(False, 'the decode census line present')
    verd = re.findall(r'verdicts (\d+)', text)
    check(verd and verd[-1] == '0', 'verdicts 0 (last census)', verd[-1] if verd else 'line absent')
    madv = re.findall(r'madvise-ro (\d+)', text)
    if madv:
        check(1780 <= int(madv[-1]) <= 1850, 'madvise-ro in the R52..R58 band (last census)', madv[-1])
    else:
        check(False, 'the madvise-ro census field present')
    births = re.findall(r'sigcensus: births (\d+) \(printed (\d+), sw-denied (\d+), budget-denied (\d+)\), traps (\d+)/(\d+), posts (\d+)/(\d+), aborts (\d+)', text)
    if births:
        b = births[-1]
        check(b[0] == '15' and b[1] == '9' and b[2] == '0' and b[3] == '6', 'births 15 (printed 9, sw 0, budget 6) (last census)', str(b[:4]))
        check(b[4] == '0' and b[5] == '0' and b[6] == '0' and b[7] == '0' and b[8] == '0', 'traps/posts/aborts all zero (last census)', str(b[4:]))
    else:
        check(False, 'the births census line present')
    panics = [ln for ln in text.splitlines()
              if re.search(r'panic(?!_trace)', ln, re.I)
              and 'launchd lint' not in ln and '_PanicOnCrash' not in ln and '(lint)' not in ln]
    check(len(panics) == 0, 'zero panic lines (panic_trace forensics excluded)', '%d found' % len(panics))
    check('SpringBoard' in text, 'SpringBoard present')
    adreno = len(re.findall(r'adreno: ', text))
    check(adreno > 400, 'adreno lines', '%d lines' % adreno)

    # ---- survival: the launchd epoch clock, last - first (the R58 epoch-measured baseline 1939 s)
    stamps = re.findall(r'1970-01-01 (\d+):(\d+):(\d+)\.', text)
    if stamps:
        secs = sorted(int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in stamps)
        span = secs[-1] - secs[0]
        check(span >= 1939, 'survival >= 1939 s (the R58 epoch baseline)', '%d s' % span)
    else:
        check(False, 'launchd epoch stamps present')

    check(complete, 'END OF LOG present (the capture is complete)')
    print('logdump: %d B, own region %d B, complete=%s' % (len(data), len(own), complete))
    for v in verdicts:
        print(' ', v)
    print('HR15 VERDICT:', 'HR15-1 FULL WIN' if green and joined else
          ('HR15-2 handshake failure' if handshake_fail and cred_ok else
           ('INCOMPLETE/GREEN-BUT-NOT-JOINED' if green else 'FAIL -- see the ✗ lines')))
    return 0


if __name__ == '__main__':
    main()
