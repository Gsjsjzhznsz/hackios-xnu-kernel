#!/usr/bin/env python3
"""scripts/r61_judge.py -- the R61 evidence-round verdict, read off the logdump bytes.

HR17 tree (pre-registered in docs/WORKLOG.md, R61 预注册):
  HR17-1 全胜 = join 链全绿 + THE NETWORK IS JOINED + 帧计数窗 (lx_station_say)
              ⇒ 计划 R37 行后半终兑现 -- the machine is ON a network.
  HR17-2      = the 67-byte (or other-length) association answer elements are read out
              in hex (the R61 evidence knife): the judge decodes the 802.11 information
              elements and names what the access point's own word says -- the next
              round's knife aims at what they say (RSN law / comeback time / reason).
  HR17-3      = the AP still silent (status 53, NO ANSWER) after the honest asks
              ⇒ the fork's "connection still unstable" risk line takes the evidence
              (microdump + wifi_diag) -- triage either way.

The kernel's own words this round (the evidence's words law -- regexes spelled from
wifi/lx/src/lx_80211.c as built):
  lx: the association asks, attempt %u of 5
  lx: ASSOCIATION: %s (status %u); we are its station number %u; its answer has %u bytes of elements, after %u asks
      %s in {THE ACCESS POINT ACCEPTS, NO ANSWER from the access point, THE ACCESS POINT REFUSES}
  lx: the association answer, bytes %u..%u: <lowercase hex pairs, 16 to a line>
  marker (MiniIOMobileFramebuffer.cpp): R61: the association answer read aloud in hex, all bytes

Privacy law: network names heard by a scan or the joined SSID are NEVER printed by the
judge -- lengths and counts only; a decoded SSID element's payload is withheld.

usage: r61_judge.py <logdump.bin|--staged 4MB capture> [--quiet]
"""
import re
import sys

R61_MARKER = 'R61: the association answer read aloud in hex, all bytes'


def own_region(data):
    start = 0
    for i in range(0, len(data), 4096):
        if data[i:i + 4096].strip(b'\x00'):
            start = i
            break
    eol = data.find(b'== END OF LOG')
    end = eol + len(b'== END OF LOG') if eol != -1 else len(data)
    return data[start:end], eol != -1


def decode_elements(hexbytes, verdicts):
    """HR17-2: name the 802.11 information elements the access point sent.
    Element = [1B id][1B len][payload].  SSID payloads are withheld (privacy law)."""
    names = {0: 'SSID', 1: 'supported rates', 3: 'DS parameter set (channel)',
             5: 'TIM', 7: 'country', 11: 'BSS load', 32: 'power capability',
             35: 'timeout interval', 42: 'ERP', 45: 'HT capabilities',
             48: 'RSN', 50: 'extended supported rates', 52: 'quiet',
             61: 'HT operation', 74: 'overlapping BSS scan params',
             127: 'extended capabilities', 191: 'VHT capabilities',
             192: 'VHT operation', 221: 'vendor specific', 255: 'extension'}
    ext_names = {2: 'protected TWTC', 3: 'next TDLS', 6: 'quiet time period',
                 8: 'SSPN', 11: 'SAE password-based cipher suites', 35: 'SAE capabilities'}
    pos, n = 0, 0
    while pos + 2 <= len(hexbytes):
        eid, ln = hexbytes[pos], hexbytes[pos + 1]
        body = hexbytes[pos + 2:pos + 2 + ln]
        if len(body) < ln:
            verdicts.append('  element decode: TRUNCATED at offset %d (id %d wants %d B, %d left)'
                            % (pos, eid, ln, len(hexbytes) - pos - 2))
            break
        nm = names.get(eid, 'extension' if eid == 255 else 'vendor/unnamed')
        detail = ''
        if eid == 255 and body:
            detail = ' ext-id %d (%s)' % (body[0], ext_names.get(body[0], '?'))
        if eid == 48 and len(body) >= 2:  # RSN: version, group cipher, pairwise count/suites, AKM count/suites, caps
            ver = body[0] | (body[1] << 8)
            detail = ' version %d, group cipher suite %02x-%02x-%02x-%02x' % (ver, body[2], body[3], body[4], body[5]) if len(body) >= 6 else ' version %d' % ver
        if eid == 0:
            detail = ' <%d B withheld by the privacy law>' % ln
        if eid == 3 and ln == 1:
            detail = ' channel %d' % body[0]
        verdicts.append('  element decode: id %3d (%s) len %2d%s' % (eid, nm, ln, detail))
        pos += 2 + ln
        n += 1
    return n


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

    # ---- the join chain (inherited, R58..R60 conservation)
    m = re.search(r'wifi: (\d+): which network to join: a name of (\d+) bytes and a passphrase of (\d+) bytes were given(\S*)', text)
    cred_ok = m is not None and 'NOT A NAME' not in (m.group(4) or '')
    check(m is not None, 'which-network-to-join line present')
    if m:
        check(cred_ok, 'credential pair passed the kernel law (name 1..32, passphrase 8..63)',
              'name %s B, passphrase %s B' % (m.group(2), m.group(3)))
        check('NOT A NAME' not in (m.group(4) or ''), 'no NOT-A-NAME suffix')
    delivery_fail = (('none was given (fw/network' in text
                      or 'no fw/network in the small image' in text
                      or 'no network to join was given' in text)
                     and m is None)
    check(not delivery_fail, 'fw/network WAS delivered (no delivery-failure line)')

    joined = 'lx: THE NETWORK IS JOINED: frames can pass' in text
    handshake_fail = 'lx: THE HANDSHAKE DID NOT COME THROUGH' in text
    keys = 'lx: THE KEYS ARE GIVEN TO THE DRIVER' in text
    assoc = ('lx: the access point is made known to the driver' in text
             or 'lx: the driver is told of the association' in text)
    check(assoc, 'association initiated (the AP is made known to the driver)')
    check(keys or joined or 'the key of the name' in text, 'WPA keys given to the driver')
    check(joined, 'THE NETWORK IS JOINED: frames can pass')

    say = re.findall(r'lx: the network is (joined|NOT joined), (\d+) s after it was joined: (\d+) frames sent \((\d+) could not be\), (\d+) received for us and (\d+) sent to everybody', text)
    if say:
        state, secs, sent, lost, recv, bcast = say[-1]
        check(state == 'joined', 'lx_station_say: joined state', '%s s, sent %s, recv %s' % (secs, sent, recv))
        check(int(sent) > 0 or int(recv) > 0, 'frames moved on the data path', 'sent %s / recv %s' % (sent, recv))
    else:
        check(False, 'lx_station_say line present (the hold window or the stop prints it)')

    radio_up = 'THE RADIO IS UP' in text and 'THE RADIO DID NOT COME UP' not in text
    check(radio_up, 'THE RADIO IS UP (the chain came up)')

    ap_wanted = re.search(r'lx: the network that is wanted: an access point on channel (\d+) \((\d+) MHz\) at (-?\d+) dBm, capabilities (0x[0-9a-f]+).*?(WPA3 SAE|WPA2|RSN)?', text)
    sae_commit = 'SAE commit response' in text
    sae_auth_ok = 'AUTHENTICATION: the access point accepts (status 0)' in text
    if ap_wanted:
        verdicts.append('  join progress: the AP is wanted on channel %s (%s MHz) at %s dBm, capabilities %s%s'
                        % (ap_wanted.group(1), ap_wanted.group(2), ap_wanted.group(3), ap_wanted.group(4),
                           (', ' + ap_wanted.group(5)) if ap_wanted.group(5) else ''))
    if sae_commit:
        verdicts.append('  join progress: SAE commit exchange %s' % ('DONE' if sae_auth_ok else 'seen'))
    if sae_auth_ok:
        verdicts.append('  join progress: the AP ACCEPTED the SAE authentication (status 0) -- the crypto law holds')
    check('ICNSS WLAN firmware READY; MSA indication received' in text, 'ICNSS WLAN firmware READY')
    check('is being brought up beside the system' in text, 'the wifi chain was brought up')

    scan_lines = len(re.findall(r'THE DRIVER IS ASKED TO SCAN', text))
    if scan_lines > 0:
        verdicts.append('! %d scan line(s) present -- join %s; if NO join attempt ran too, wifi_join did not parse (HR15-4)'
                        % (scan_lines, 'ran and won' if (joined or handshake_fail or keys) else 'NEVER ran'))

    # ---- THE R61 EVIDENCE BLOCK (this round's knife)
    check(R61_MARKER in text, 'the R61 association marker present (the evidence round rode along)')
    asks = re.findall(r'lx: the association asks, attempt (\d+) of 5', text)
    check(len(asks) > 0, 'the per-ask self-report lines present (the honest asks)', '%d ask line(s)' % len(asks))
    if asks:
        seq = [int(a) for a in asks]
        check(seq == list(range(1, len(seq) + 1)), 'the ask lines count up from 1 (no lost asks)', str(seq))
    summ = re.search(r'lx: ASSOCIATION: (THE ACCESS POINT ACCEPTS|NO ANSWER from the access point|THE ACCESS POINT REFUSES)'
                     r' \(status (\d+)\); we are its station number (\d+); its answer has (\d+) bytes of elements, after (\d+) asks', text)
    answer_word, status, aid, nbytes, nasks = (None,) * 5
    if summ:
        answer_word, status, aid, nbytes, nasks = summ.group(1), int(summ.group(2)), summ.group(3), int(summ.group(4)), int(summ.group(5))
        verdicts.append('  R61 association summary: %s, status %d, station number %s, %d bytes of elements, after %d asks'
                        % (answer_word, status, aid, nbytes, nasks))
        check(nasks == len(asks), 'the honest ask count equals the per-ask lines (%d)' % nasks)
    else:
        check(False, 'the R61 association summary line present')

    hexlines = re.findall(r'lx: the association answer, bytes (\d+)\.\.(\d+): ((?:[0-9a-f]{2} )+)', text)
    hexbytes = bytearray()
    for lo, hi, hh in hexlines:
        hexbytes += bytes.fromhex(hh.replace(' ', ''))
    if answer_word is not None and answer_word != 'THE ACCESS POINT ACCEPTS':
        # a refusal/silence that carries IEs is the AP's own word on WHY -- the dump MUST be present
        if nbytes:
            check(len(hexlines) > 0, 'the association answer hex dump present', '%d line(s), %d B total' % (len(hexlines), len(hexbytes)))
            check(len(hexbytes) == nbytes, 'the hex dump length equals the summary bytes', '%d vs %d' % (len(hexbytes), nbytes))
            if hexbytes:
                verdicts.append('  R61 evidence: the access point answer elements, %d B in %d hex lines:' % (len(hexbytes), len(hexlines)))
                for lo, hi, hh in hexlines:
                    verdicts.append('    bytes %s..%s: %s' % (lo, hi, hh.rstrip()))
                nelem = decode_elements(bytes(hexbytes), verdicts)
                verdicts.append('  element decode: %d element(s) parsed -- HR17-2 names the next knife' % nelem)
        else:
            check(len(hexlines) == 0, 'no elements carried -- the silence is bare (nothing to read)')
    elif answer_word == 'THE ACCESS POINT ACCEPTS':
        check(len(hexlines) == 0, 'accepted -- the dump branch stays silent (the elements went to station_abilities)')

    if handshake_fail:
        green = False
        verdicts.append('HR15-2 SHAPE: the handshake did not come through (a wrong passphrase gives this, per the fork);'
                        ' also check the security law: lx_station_join is WPA2-PSK only (WPA3-only APs will refuse)')
    elif summ is not None and answer_word == 'THE ACCESS POINT ACCEPTS' and not joined:
        verdicts.append('! the AP ACCEPTED the association but THE NETWORK IS JOINED never printed -- the join '
                        'stopped between association and the keys (a NEW wall, triage the post-assoc path)')
        green = False
    elif summ is not None and answer_word != 'THE ACCESS POINT ACCEPTS':
        green = False
        verdicts.append('HR17-2/HR17-3 SHAPE: the AP %s after %d asks (status %d): the answer elements (%d B) are '
                        'read aloud above -- the next round\'s knife aims at what they say (RSN law / comeback / reason); '
                        'the join chain itself (radio, SAE, keys) is ALIVE'
                        % ('stayed SILENT' if answer_word.startswith('NO ANSWER') else 'REFUSED', nasks, status, nbytes))
    elif delivery_fail and not joined:
        green = False
        verdicts.append('HR15 VERDICT SHAPE: FAIL on the fw/network delivery (HR15-3/HR15-0) -- the join chain never started')

    # ---- the inheritance table (R54..R60 conservation) -- the census reprints every minute, take the LAST
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
        check(1780 <= int(madv[-1]) <= 1850, 'madvise-ro in the R52..R60 band (last census)', madv[-1])
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

    # ---- survival: the launchd epoch clock, last - first (the R60 epoch-measured record 2094 s)
    stamps = re.findall(r'1970-01-01 (\d+):(\d+):(\d+)\.', text)
    if stamps:
        secs = sorted(int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in stamps)
        span = secs[-1] - secs[0]
        check(span >= 2094, 'survival >= 2094 s (the R60 epoch record)', '%d s' % span)
    else:
        check(False, 'launchd epoch stamps present')

    check(complete, 'END OF LOG present (the capture is complete)')
    print('logdump: %d B, own region %d B, complete=%s%s' % (len(data), len(own), complete, ' [staged]' if staged else ''))
    for v in verdicts:
        print(' ', v)
    if green and joined:
        print('HR17 VERDICT: HR17-1 FULL WIN -- THE NETWORK IS JOINED; the plan\'s R37 row, second half, is DONE')
    elif summ is not None and answer_word != 'THE ACCESS POINT ACCEPTS':
        print('HR17 VERDICT: HR17-2/HR17-3 -- the evidence round read the answer; aim the next knife at the elements above')
    elif handshake_fail and cred_ok:
        print('HR17 VERDICT: HR15-2 handshake failure shape (inherited tree)')
    else:
        print('HR17 VERDICT: INCOMPLETE/FAIL -- see the ✗ lines')
    return 0


if __name__ == '__main__':
    main()
