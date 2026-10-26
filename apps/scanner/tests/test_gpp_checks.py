"""Tests for decoding GPP strings and the GPP and GPC checks.

The encoded headers and sections come from the IAB reference library's
own tests (iabgpp-es), so decoding is checked against the specification
rather than against the banner.
"""

import pytest

from src.consent_results import Expectations, PhaseObservation
from src.consent_validator import CONSENT_COOKIE, judge
from src.gpp_checks import GppDecodeError, decode_gpp, decode_header, decode_section

# usnat with every opt-out set to 1 (opted out) and GPC on, version 2.
USNAT_OPTED_OUT_GPC = "CVVVkkkkkpFY.YA"
# The same, version 1, with no GPC sub-section.
USNAT_V1_OPTED_OUT = "BVVVkkkkkpFY"
# usnat version 2 with every opt-out 2 (did not opt out). Its bits after
# the version are 01 01 01 01 00 00 10 10 10, read by hand.
USNAT_NOT_OPTED_OUT = "CVQqAAAACg"
# Header listing section 7 (usnat) alone.
HEADER_USNAT = "DBABLA"


class TestHeader:
    @pytest.mark.parametrize(
        ("encoded", "ids"), [("DBAA", []), ("DBABMA", [2]), ("DBACNYA", [2, 6])]
    )
    def test_decodes_the_reference_vectors(self, encoded, ids):
        assert decode_header(encoded) == ids

    def test_decodes_usnat(self):
        assert decode_header(HEADER_USNAT) == [7]

    def test_the_old_banner_header_is_not_read_as_section_2(self):
        # The banner used to encode section 2 with a six-bit count.
        try:
            assert decode_header("DBBMAA") != [2]
        except GppDecodeError:
            pass

    @pytest.mark.parametrize("encoded", ["", "AAAA", "DB", "D*AA"])
    def test_rejects_what_is_not_a_header(self, encoded):
        with pytest.raises(GppDecodeError):
            decode_header(encoded)


class TestSection:
    def test_reads_opt_outs_and_gpc(self):
        fields = decode_section("usnat", USNAT_OPTED_OUT_GPC)
        assert fields["Version"] == 2
        assert (fields["SaleOptOut"], fields["SharingOptOut"]) == (1, 1)
        assert fields["TargetedAdvertisingOptOut"] == 1
        assert fields["Gpc"] == 1

    def test_reads_version_one(self):
        fields = decode_section("usnat", USNAT_V1_OPTED_OUT)
        assert fields["Version"] == 1
        assert fields["SaleOptOut"] == 1
        assert "Gpc" not in fields

    def test_reads_did_not_opt_out(self):
        fields = decode_section("usnat", USNAT_NOT_OPTED_OUT)
        assert (fields["SaleOptOut"], fields["SharingOptOut"]) == (2, 2)
        assert fields["TargetedAdvertisingOptOut"] == 2

    def test_whole_string(self):
        assert decode_gpp(f"{HEADER_USNAT}~{USNAT_NOT_OPTED_OUT}")["usnat"]["SharingOptOut"] == 2

    def test_header_and_sections_must_agree(self):
        with pytest.raises(GppDecodeError):
            decode_gpp(f"DBACNYA~{USNAT_NOT_OPTED_OUT}")


GPP = Expectations(gpp_enabled=True, gpp_sections=["usnat"])


def _usnat(opt_out: int, gpc: int | None = None) -> str:
    """A usnat v1 core with every notice 1 and the given opt-out value."""
    bits = f"{1:06b}" + "01" * 6 + f"{opt_out:02b}" * 3 + "00" * 12 + "00" * 2 + "00" * 4
    bits += "0" * (-len(bits) % 6)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    core = "".join(alphabet[int(bits[i : i + 6], 2)] for i in range(0, len(bits), 6))
    if gpc is None:
        return core
    return core + "." + ("Y" if gpc else "Q") + "A"


def gpp(gpp_string: str, supported=("7:usnat",)) -> dict:
    return {
        "ping": {"cmpStatus": "loaded", "supportedAPIs": list(supported), "gppString": gpp_string}
    }


def before(**kwargs):
    kwargs.setdefault("gpp", gpp(""))
    return PhaseObservation(
        "before_consent", loaded=True, banner_found=True, observed=True, **kwargs
    )


def chosen(phase: str, opt_out: int, **kwargs):
    kwargs.setdefault("gpp", gpp(f"{HEADER_USNAT}~{_usnat(opt_out, gpc=0)}"))
    kwargs.setdefault("cookies", [{"name": CONSENT_COOKIE, "domain": "example.com"}])
    return PhaseObservation(
        phase, loaded=True, banner_found=True, clicked=True, observed=True, **kwargs
    )


def checks(expect=GPP, b=None, a=None, r=None, g=None) -> dict:
    found = judge(
        expect, b or before(), a or chosen("after_accept", 2), r or chosen("after_reject", 1), g
    )
    return {c.key: c for c in found}


class TestTheHelperMatchesTheReference:
    def test_a_built_section_decodes_as_expected(self):
        assert decode_section("usnat", _usnat(1, gpc=1))["SaleOptOut"] == 1
        assert decode_section("usnat", _usnat(2))["TargetedAdvertisingOptOut"] == 2


class TestGppChecks:
    def test_correct_signalling_passes(self):
        found = checks()
        for key in ("before_consent.gpp_api", "after_accept.gpp", "after_reject.gpp"):
            assert found[key].status == "passed", (key, found[key])

    def test_checks_do_not_apply_when_gpp_is_off(self):
        found = checks(expect=Expectations())
        for key in ("before_consent.gpp_api", "after_accept.gpp", "after_reject.gpp"):
            assert found[key].status == "not_applicable"

    def test_missing_api_fails(self):
        assert checks(b=before(gpp=None))["before_consent.gpp_api"].status == "failed"

    def test_unanswered_ping_is_inconclusive(self):
        b = before(gpp={"ping": {"timedOut": True}})
        assert checks(b=b)["before_consent.gpp_api"].status == "inconclusive"

    def test_missing_supported_section_fails(self):
        b = before(gpp=gpp("", supported=["8:usca"]))
        check = checks(b=b)["before_consent.gpp_api"]
        assert check.status == "failed"
        assert check.evidence == {"missing": ["usnat"]}

    def test_sections_without_their_ids_fail(self):
        check = checks(b=before(gpp=gpp("", supported=["usnat"])))["before_consent.gpp_api"]
        assert check.status == "failed"
        assert check.evidence == {"wrong": ["usnat"]}
        assert "7:usnat" in check.fix

    def test_sections_with_the_wrong_id_fail(self):
        check = checks(b=before(gpp=gpp("", supported=["8:usnat"])))["before_consent.gpp_api"]
        assert check.evidence == {"wrong": ["8:usnat"]}

    def test_other_sections_are_left_alone(self):
        b = before(gpp=gpp("", supported=["7:usnat", "2:tcfeuv2", "usca"]))
        assert checks(b=b)["before_consent.gpp_api"].status == "passed"

    def test_no_string_after_accept_fails(self):
        assert checks(a=chosen("after_accept", 2, gpp=gpp("")))["after_accept.gpp"].status == (
            "failed"
        )

    def test_a_string_the_specification_cannot_read_fails(self):
        a = chosen("after_accept", 2, gpp=gpp(f"DBBLBLA~{_usnat(2)}"))
        check = checks(a=a)["after_accept.gpp"]
        assert check.status == "failed"
        assert "specification" in check.fix

    def test_missing_section_fails(self):
        expect = Expectations(gpp_enabled=True, gpp_sections=["usnat", "usca"])
        assert checks(expect=expect)["after_accept.gpp"].status == "failed"

    def test_opt_out_after_accept_fails(self):
        check = checks(a=chosen("after_accept", 1))["after_accept.gpp"]
        assert check.status == "failed"
        assert "usnat SaleOptOut" in check.evidence["fields"]

    def test_no_opt_out_after_reject_fails(self):
        assert checks(r=chosen("after_reject", 2))["after_reject.gpp"].status == "failed"

    def test_usnat_is_expected_when_the_site_lists_no_sections(self):
        expect = Expectations(gpp_enabled=True)
        assert checks(expect=expect)["after_accept.gpp"].status == "passed"


GPC = Expectations(
    gpp_enabled=True, gpp_sections=["usnat"], gpc_enabled=True, gpc_global_honour=True
)


def with_gpc(*, detected=True, honoured=True, flag=1):
    return PhaseObservation(
        "with_gpc",
        loaded=True,
        banner_found=True,
        clicked=True,
        observed=True,
        consent_state={"gpcDetected": detected, "gpcHonoured": honoured},
        gpp=gpp(f"{HEADER_USNAT}~{_usnat(2, gpc=flag)}"),
    )


class TestGpc:
    def test_recorded_signal_passes(self):
        assert checks(expect=GPC, g=with_gpc())["with_gpc.recorded"].status == "passed"

    def test_does_not_apply_when_gpc_is_off(self):
        assert checks(g=with_gpc())["with_gpc.recorded"].status == "not_applicable"

    def test_not_run_is_inconclusive(self):
        assert checks(expect=GPC)["with_gpc.recorded"].status == "inconclusive"

    def test_signal_not_noted_fails(self):
        check = checks(expect=GPC, g=with_gpc(detected=False))["with_gpc.recorded"]
        assert check.status == "failed"
        assert "doesn't note the GPC signal" in check.detail

    def test_honoured_everywhere_but_not_honoured_fails(self):
        check = checks(expect=GPC, g=with_gpc(honoured=False))["with_gpc.recorded"]
        assert check.status == "failed"

    def test_honouring_is_not_judged_when_it_depends_on_the_region(self):
        expect = Expectations(gpc_enabled=True)
        assert checks(expect=expect, g=with_gpc(honoured=False))["with_gpc.recorded"].status == (
            "passed"
        )

    def test_gpp_flag_not_set_fails(self):
        check = checks(expect=GPC, g=with_gpc(flag=0))["with_gpc.recorded"]
        assert check.status == "failed"
        assert "GPC flag" in check.detail
