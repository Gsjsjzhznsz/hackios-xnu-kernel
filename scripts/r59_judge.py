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
    # the delivery-failure shapes (the attempt-1 law): ALL three name a failed fw/network
    # delivery -- redwood_wifi_network()'s own "none was given (fw/network", the
    # image_file() helper's "no fw/network in the small image", and join_given()'s
    # "no network to join was given (fw-wifi/network".  (The attempt-1 judge matched the
    # wifi_mac address line's "none was given" here -- a false green; all three real
    # shapes name the file, never the radio's address.)
    delivery_fail = (('none was given (fw/network' in text
                      or 'no fw/network in the small image' in text
                      or 'no network to join was given' in text)
                     and m is None)   # the attempt-2 law: a success line PROVES delivery -- the stale-ring
                                      # remnants of earlier boots lose to it
    check(not delivery_fail, 'fw/network WAS delivered (no delivery-failure line)')
    if 'no fw/network in the small image' in text and m is None:
        verdicts.append('HR15-0 SHAPE: "no fw/network in the small image" -- the staged flashboot never reached md1:\n'
                        '    a STAGING failure, not a credential failure.  The loader merged its stale DRAM staging buffer\n'
                        '    (the attempt-1 law: fastboot stage raced the post-oem-ramdisk USB re-enumeration door).\n'
                        '    Remedy: r59_session v2 -- door before every fastboot call, OKAY-gated stages, abort on failure.')
    elif delivery_fail:
        verdicts.append('HR15-3 SHAPE: the fw/network delivery failed (the staged flashboot lacks the file, or the\n'
                        '    sizes break the kernel law); the join-chain misses below are a consequence.')

    joined = 'lx: THE NETWORK IS JOINED: frames can pass' in text
    handshake_fail = 'lx: THE HANDSHAKE DID NOT COME THROUGH' in text
    keys = 'lx: THE KEYS ARE GIVEN TO THE DRIVER' in text
    assoc = ('lx: the access point is made known to the driver' in text
             or 'lx: the driver is told of the association' in text)
    check(assoc, 'association initiated (the AP is made known to the driver)')
    check(keys or joined or 'the key of the name' in text, 'WPA keys given to the driver')
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
    # the radio-up gate: the attempt-2 law -- a join refusal INSIDE step 15 makes the kernel
    # print "THE RADIO DID NOT COME UP: step 15 failed"; the plain substring "THE RADIO IS UP"
    # can also sit in stale-ring remnants, so the failure line wins when present
    radio_up = 'THE RADIO IS UP' in text and 'THE RADIO DID NOT COME UP' not in text
    check(radio_up, 'THE RADIO IS UP (the chain came up)')

    # the join progress chain (the attempt-2 evidence lines -- the SAE path is ALIVE):
    ap_wanted = re.search(r'lx: the network that is wanted: an access point on channel (\d+) \((\d+) MHz\) at (-?\d+) dBm, capabilities (0x[0-9a-f]+).*?(WPA3 SAE|WPA2|RSN)?', text)
    sae_commit = 'SAE commit response' in text
    sae_auth_ok = 'AUTHENTICATION: the access point accepts (status 0)' in text
    assoc_refuse = re.search(r'lx: ASSOCIATION: THE ACCESS POINT REFUSES \(status (\d+)\)', text)
    assoc_accept = 'lx: ASSOCIATION' in text and not assoc_refuse and ('THE ACCESS POINT ACCEPTS' in text or 'associated' in text)
    if ap_wanted:
        verdicts.append('  join progress: the AP is wanted on channel %s (%s MHz) at %s dBm, capabilities %s%s'
                        % (ap_wanted.group(1), ap_wanted.group(2), ap_wanted.group(3), ap_wanted.group(4),
                           (', ' + ap_wanted.group(5)) if ap_wanted.group(5) else ''))
    if sae_commit:
        verdicts.append('  join progress: SAE commit exchange %s' % ('DONE' if sae_auth_ok else 'seen'))
    if sae_auth_ok:
        verdicts.append('  join progress: the AP ACCEPTED the SAE authentication (status 0) -- the crypto law holds')
    if assoc_refuse:
        verdicts.append('  join progress: the ASSOCIATION REFUSED (status %s) -- the AP rejects the association after'
                        ' accepting the SAE authentication: the assoc-side RSN/AKM/PMF law is unmet (HR15-5)' % assoc_refuse.group(1))
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
    elif assoc_refuse:
        green = False
        verdicts.append('HR15-5 SHAPE: the AP accepted the SAE authentication and REFUSED the association (status %s):'
                        ' the join reached the AP and the wall is the association law (RSN IE with the SAE AKM + PMF),'
                        ' not the credentials and not the radio -- R60\'s knife: the fork\'s sae assoc-side law (r36work)'
                        ' + the RSN IE construction (AKM 8, PMF required) + the SAE confirm-before-assoc ordering'
                        % (assoc_refuse.group(1) if assoc_refuse else '?'))
    elif delivery_fail and not joined:
        green = False
        verdicts.append('HR15 VERDICT SHAPE: FAIL on the fw/network delivery (HR15-3/HR15-0) -- the join chain never started')

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
