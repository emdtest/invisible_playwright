"""A page that holds a media permission reads the DECLARED network in its ICE host candidates, never an address of the machine.

Retail Firefox hides the host address of a connection behind an mDNS name, except for a page that has a camera or a microphone
permission (or an active capture): ``PeerConnectionImpl::GetPrefObfuscateHostAddresses`` is false while
``MediaManager::IsActivelyCapturingOrHasAPermission(window)`` is true, and the same condition lifts ``default_address_only``. A
page that has called ``getUserMedia`` then reads ``a=candidate ... typ host`` with the raw ADDRESS of every adapter the gathering
returned. The gathering is the content process asking the parent (``StunAddrsRequestParent``, ``GetStunAddrs``), which answers with
``nr_stun_get_addrs``: the interfaces the core DECLARED in ``STEALTHFOX_WEBRTC_NETWORK`` (series:
``fingerprint/webrtc/declared-network``, hooks in ``nICEr/src/stun/addrs.cpp`` and ``ice_ctx.cpp``), never the ones of the host.

What this fixes (green on firefox-68), so that the rebuilt engine cannot change it:

  * ``v4=<address>;v6=0``: the host addresses the page reads are exactly that one IPv4 address;
  * ``v4=<address>;v6=1;p6=<prefix>/64``: exactly that IPv4 address and ONE IPv6 address inside the prefix (the interface identifier is
    drawn at random once per process, so the address is not fixed, the prefix is);
  * no address of the machine the browser runs on (read with ``psutil.net_if_addrs``) is among them;
  * negative controls in the same test: before the permission the very same page reads only mDNS names (the permission is what
    exposes the address, so the raw address is the product's), and a second declaration gives the page the OTHER address.

The declared addresses are ones no runner has (a private /8 and /12 block chosen off the common LAN ranges). The permission is the one
a test can give a page without a prompt (``media.navigator.permission.disabled``) on the fake devices the core declares
(``media.navigator.streams.fake``); the capture stays open while the connection gathers. Hermetic: a local TCP-only SOCKS5 proxy and a
local page, no external network.
"""
from __future__ import annotations

import ipaddress

import pytest

from _waits import wait_until
from test_webrtc_realness import _e2e_binary, _launch, candidates, host_candidates, is_mdns

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("the_udp_line")]

NETWORK_ENV = "STEALTHFOX_WEBRTC_NETWORK"
PREFIX_A = "2a01:e0a:1f2:3450::/64"
PREFIX_B = "2a02:1810:4f0:7a00::/64"
V4_A, V4_B = "10.211.37.42", "172.29.201.9"
V4_ONLY_A = "v4=%s;v6=0;p6=" % V4_A
DUAL_A = "v4=%s;v6=1;p6=%s" % (V4_A, PREFIX_A)
DUAL_B = "v4=%s;v6=1;p6=%s" % (V4_B, PREFIX_B)

# The page gathers once with no permission (the obfuscated read), asks for the microphone, and gathers again while the capture is open.
# The page starts the reads and stores the answer; the test waits for it (``wait_until``), the page runs no timer.
_READ_JS = r"""() => {
  window.__read = null;
  (async () => {
  const gather = async () => {
    const pc = new RTCPeerConnection({iceServers: []});
    pc.createDataChannel('');
    const cands = [];
    pc.addEventListener('icecandidate', e => { if (e.candidate && e.candidate.candidate) cands.push(e.candidate.candidate); });
    await pc.setLocalDescription(await pc.createOffer());
    // Until the gathering says it is over; the deadline of the wait is the test's.
    await new Promise(resolve => {
      const done = () => { if (pc.iceGatheringState === 'complete') resolve(); };
      pc.addEventListener('icegatheringstatechange', done);
      done();
    });
    try { pc.close(); } catch (e) {}
    return cands;
  };
  const before = await gather();
  let stream = null, error = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch (e) { error = String(e); }
  const after = stream ? await gather() : [];
  if (stream) stream.getTracks().forEach(t => t.stop());
  window.__read = {before, after, error};
  })().catch(e => { window.__read = {before: [], after: [], error: String(e)}; });
}"""


def _session(declaration, socks, page_url, monkeypatch, seed=42):
    """One proxied launch with the declaration in the environment and the media permission given; the page reads its host candidates."""
    binary = _e2e_binary()
    if not binary:
        pytest.skip("no patched binary (set STEALTHFOX_E2E_BINARY)")
    monkeypatch.setenv(NETWORK_ENV, declaration)
    prefs = {"network.proxy.allow_hijacking_localhost": True, "network.proxy.no_proxies_on": "",
             "media.peerconnection.ice.obfuscate_host_addresses": True,
             "media.navigator.permission.disabled": True}
    proxy = {"server": "socks5://127.0.0.1:%d" % socks.port}
    with _launch(binary_path=binary, proxy=proxy, extra_prefs=prefs, seed=seed) as browser:
        page = browser.new_context().new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.evaluate(_READ_JS)
        # Until the page has the answer (the gathering twice, and the fake microphone): a deadline far beyond any healthy run.
        assert wait_until(lambda: page.evaluate("window.__read !== null"), 60, lambda: page.wait_for_timeout(100)), (
            "the page never finished reading its host candidates")
        read = page.evaluate("window.__read")
    assert read["error"] is None, "the page could not open the fake microphone: %r" % read["error"]
    return host_candidates(candidates(read["before"])), host_candidates(candidates(read["after"]))


def _runner_addresses():
    import psutil

    found = set()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            try:
                found.add(ipaddress.ip_address(a.address.split("%")[0]))
            except ValueError:
                pass  # a link-layer address
    return found


def _read_addresses(hosts):
    return {ipaddress.ip_address(c["address"]) for c in hosts}


def _split(addresses):
    return ({a for a in addresses if a.version == 4}, {a for a in addresses if a.version == 6})


@pytest.mark.parametrize("declaration, v4, prefix", [(V4_ONLY_A, V4_A, None), (DUAL_A, V4_A, PREFIX_A)],
                         ids=["without-ipv6", "with-ipv6"])
def test_a_page_with_a_media_permission_reads_the_declared_addresses(
        local_https_page, socks5_tcp_only, monkeypatch, declaration, v4, prefix):
    """The raw host addresses of the page are the declared ones, and none of the machine's."""
    before, after = _session(declaration, socks5_tcp_only, local_https_page, monkeypatch)
    # Control: without the permission the same page reads names, so the addresses below are what the permission exposes.
    assert before and all(is_mdns(c["address"]) for c in before), [c["address"] for c in before]
    assert after, "no host candidate after the permission"
    assert not any(is_mdns(c["address"]) for c in after), [c["address"] for c in after]
    seen_v4, seen_v6 = _split(_read_addresses(after))
    assert seen_v4 == {ipaddress.ip_address(v4)}, sorted(map(str, seen_v4))
    if prefix is None:
        assert not seen_v6, sorted(map(str, seen_v6))
    else:
        assert len(seen_v6) == 1 and all(a in ipaddress.ip_network(prefix) for a in seen_v6), sorted(map(str, seen_v6))
    # The declared address is the one of a UDP and of a TCP host candidate: the page reads it on both.
    assert {c["proto"] for c in after if c["address"] == v4} == {"UDP", "TCP"}, after
    assert not (_runner_addresses() & _read_addresses(after)), (sorted(map(str, _runner_addresses())), after)


def test_another_declaration_gives_the_page_the_other_addresses(local_https_page, socks5_tcp_only, monkeypatch):
    """Negative control: the addresses follow the declaration. A second launch declared with another IPv4 address and another prefix
    reads that address and that prefix, and none of the first one's."""
    _, after_a = _session(DUAL_A, socks5_tcp_only, local_https_page, monkeypatch)
    _, after_b = _session(DUAL_B, socks5_tcp_only, local_https_page, monkeypatch)
    v4_a, v6_a = _split(_read_addresses(after_a))
    v4_b, v6_b = _split(_read_addresses(after_b))
    assert v4_a == {ipaddress.ip_address(V4_A)} and v4_b == {ipaddress.ip_address(V4_B)}, (v4_a, v4_b)
    assert len(v6_a) == 1 and all(a in ipaddress.ip_network(PREFIX_A) for a in v6_a), v6_a
    assert len(v6_b) == 1 and all(a in ipaddress.ip_network(PREFIX_B) for a in v6_b), v6_b
    assert not (_read_addresses(after_a) & _read_addresses(after_b))
    assert not (_runner_addresses() & (_read_addresses(after_a) | _read_addresses(after_b)))
