"""Form ADV Part 1A field dictionary.

The SEC's bulk CSV labels most columns with bare Form ADV item codes ("5F(2)(c)",
"11D(4)"). This module maps those codes to human labels, types, and grouping, so
the rest of the pipeline never has to hardcode an item code's meaning.

Every label carries `verified`: True means the wording was taken directly from the
text of Form ADV Part 1A (reference/formadv-part1a.txt, extracted from the SEC's
own PDF). Nothing with verified=False may be published on the site -- it is a
placeholder to be grounded later.

Types:
    yn     "Y"/"N" checkbox
    int    whole number
    money  US dollars, stored in the CSV as "1,234,567.00"
    pct    percentage
    band   a bucketed range rather than an exact figure
    text   free text
    date   date
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    code: str
    label: str
    item: str
    group: str
    dtype: str
    verified: bool = True
    publish: bool = True


_FIELDS: dict[str, Field] = {}


def _add(
    code: str,
    label: str,
    item: str,
    group: str,
    dtype: str,
    *,
    verified: bool = True,
    publish: bool = True,
) -> None:
    _FIELDS[code] = Field(code, label, item, group, dtype, verified, publish)


# --------------------------------------------------------------------------
# Item 3 / 4 -- organization and succession
# --------------------------------------------------------------------------
_add("3A", "Form of organization", "3", "Organization", "text")
_add("3B", "Fiscal year end month", "3", "Organization", "text")
_add("4A", "Succeeding to the business of another registered adviser", "4", "Organization", "yn")
_add("4B", "Date of succession", "4", "Organization", "date")

# --------------------------------------------------------------------------
# Item 5A/5B -- employees
# --------------------------------------------------------------------------
_add("5A", "Total employees (excluding clerical)", "5", "Employees", "int")
_EMPLOYEE_SUB = {
    "1": "Employees performing investment advisory functions (including research)",
    "2": "Employees who are registered representatives of a broker-dealer",
    "3": "Employees registered as investment adviser representatives with a state",
    "4": "Employees registered as IARs for another investment adviser",
    "5": "Employees who are licensed insurance agents",
    "6": "Firms or persons who solicit advisory clients on the firm's behalf",
}
for _n, _label in _EMPLOYEE_SUB.items():
    _add(f"5B({_n})", _label, "5", "Employees", "int")

# --------------------------------------------------------------------------
# Item 5C -- clients without RAUM
# --------------------------------------------------------------------------
_add("5C(1)", "Clients served without regulatory assets under management", "5", "Clients", "int")
_add("5C(2)", "Percentage of clients who are non-U.S. persons", "5", "Clients", "pct")

# --------------------------------------------------------------------------
# Item 5D -- client types.  Matrix of client category x (count, <5 flag, RAUM).
# --------------------------------------------------------------------------
CLIENT_TYPES = {
    "a": "Individuals (other than high net worth individuals)",
    "b": "High net worth individuals",
    "c": "Banking or thrift institutions",
    "d": "Investment companies",
    "e": "Business development companies",
    "f": "Pooled investment vehicles (other than investment companies and BDCs)",
    "g": "Pension and profit sharing plans (not the participants or government plans)",
    "h": "Charitable organizations",
    "i": "State or municipal government entities (including government pension plans)",
    "j": "Other investment advisers",
    "k": "Insurance companies",
    "l": "Sovereign wealth funds and foreign official institutions",
    "m": "Corporations or other businesses not listed above",
    "n": "Other",
}
_5D_MEASURE = {
    "1": ("Number of clients", "int"),
    "2": ("Fewer than 5 clients", "yn"),
    "3": ("Regulatory assets under management", "money"),
}
# Form ADV Item 5.D.: the "fewer than 5 clients" shortcut is not offered for
# categories (d), (e) and (f), so those columns do not exist in the bulk file.
_NO_FEWER_THAN_FIVE = {"d", "e", "f"}
for _letter, _client in CLIENT_TYPES.items():
    for _num, (_measure, _dtype) in _5D_MEASURE.items():
        if _num == "2" and _letter in _NO_FEWER_THAN_FIVE:
            continue
        _add(
            f"5D({_letter})({_num})",
            f"{_client} - {_measure}",
            "5",
            "Client types",
            _dtype,
        )

# Older Form ADV versions laid Item 5D out transposed: 5D(1)(x) = number of
# clients of type x, 5D(2)(x) = the "fewer than 5" flag.  Both layouts appear in
# the bulk file because firms file under different form versions.
for _letter, _client in CLIENT_TYPES.items():
    if _letter == "n":
        continue
    _add(f"5D(1)({_letter})", f"{_client} - Number of clients (legacy layout)", "5", "Client types", "int")
    _add(f"5D(2)({_letter})", f"{_client} - Fewer than 5 clients (legacy layout)", "5", "Client types", "yn")

# --------------------------------------------------------------------------
# Item 5E -- compensation arrangements.  Drives "fee-only" classification.
# --------------------------------------------------------------------------
COMPENSATION = {
    "1": "A percentage of assets under management",
    "2": "Hourly charges",
    "3": "Subscription fees (for a newsletter or periodical)",
    "4": "Fixed fees (other than subscription fees)",
    "5": "Commissions",
    "6": "Performance-based fees",
    "7": "Other",
}
for _n, _label in COMPENSATION.items():
    _add(f"5E({_n})", f"Compensated by: {_label}", "5", "Compensation", "yn")

# --------------------------------------------------------------------------
# Item 5F -- regulatory assets under management
# --------------------------------------------------------------------------
_add("5F(1)", "Provides continuous and regular supervisory or management services", "5", "Assets", "yn")
_add("5F(2)(a)", "Discretionary regulatory assets under management", "5", "Assets", "money")
_add("5F(2)(b)", "Non-discretionary regulatory assets under management", "5", "Assets", "money")
_add("5F(2)(c)", "Total regulatory assets under management", "5", "Assets", "money")
_add("5F(2)(d)", "Discretionary accounts", "5", "Assets", "int")
_add("5F(2)(e)", "Non-discretionary accounts", "5", "Assets", "int")
_add("5F(2)(f)", "Total accounts", "5", "Assets", "int")
_add("5F(3)", "Regulatory assets under management attributable to non-U.S. persons", "5", "Assets", "money")

# --------------------------------------------------------------------------
# Item 5G -- advisory services offered
# --------------------------------------------------------------------------
ADVISORY_SERVICES = {
    "1": "Financial planning services",
    "2": "Portfolio management for individuals and/or small businesses",
    "3": "Portfolio management for investment companies",
    "4": "Portfolio management for pooled investment vehicles",
    "5": "Portfolio management for businesses or institutional clients",
    "6": "Pension consulting services",
    "7": "Selection of other advisers (including private fund managers)",
    "8": "Publication of periodicals or newsletters",
    "9": "Security ratings or pricing services",
    "10": "Market timing services",
    "11": "Educational seminars/workshops",
    "12": "Other",
}
for _n, _label in ADVISORY_SERVICES.items():
    _add(f"5G({_n})", f"Offers: {_label}", "5", "Services", "yn")

# --------------------------------------------------------------------------
# Item 5H-5L
# --------------------------------------------------------------------------
_add("5H", "Financial planning clients in last fiscal year", "5", "Services", "band")
_add("5I(1)", "Participates in a wrap fee program", "5", "Wrap programs", "yn")
_add("5I(2)(a)", "Regulatory AUM as sponsor to a wrap fee program", "5", "Wrap programs", "money")
_add("5I(2)(b)", "Regulatory AUM as portfolio manager for a wrap fee program", "5", "Wrap programs", "money")
_add("5I(2)(c)", "Regulatory AUM as both sponsor and portfolio manager for the same wrap fee program", "5", "Wrap programs", "money")
_add("5J(1)", "Advises only on limited types of investments", "5", "Scope", "yn")
_add("5J(2)", "Reports client assets computed differently from regulatory AUM", "5", "Scope", "yn")
_add("5K(1)", "Has regulatory AUM attributable to separately managed account clients", "5", "Separately managed accounts", "yn")
_add("5K(2)", "Engages in borrowing transactions for separately managed account clients", "5", "Separately managed accounts", "yn")
_add("5K(3)", "Engages in derivative transactions for separately managed account clients", "5", "Separately managed accounts", "yn")
_add("5K(4)", "A single custodian holds 10% or more of remaining regulatory AUM", "5", "Separately managed accounts", "yn")

MARKETING = {
    "a": "Performance results",
    "b": "A reference to specific investment advice provided by the firm",
    "c": "Testimonials",
    "d": "Endorsements",
    "e": "Third-party ratings",
}
for _n, _label in MARKETING.items():
    _add(f"5L(1)({_n})", f"Advertisements include: {_label}", "5", "Marketing", "yn")
_add("5L(2)", "Compensates for testimonials, endorsements, or third-party ratings", "5", "Marketing", "yn")
_add("5L(3)", "Advertisements include hypothetical performance", "5", "Marketing", "yn")
_add("5L(4)", "Advertisements include predecessor performance", "5", "Marketing", "yn")

# --------------------------------------------------------------------------
# Item 6 -- other business activities
# --------------------------------------------------------------------------
OTHER_BUSINESS = {
    "1": "Broker-dealer (registered or unregistered)",
    "2": "Registered representative of a broker-dealer",
    "3": "Commodity pool operator or commodity trading advisor",
    "4": "Futures commission merchant",
    "5": "Real estate broker, dealer, or agent",
    "6": "Insurance broker or agent",
    "7": "Bank (including a separately identifiable department or division)",
    "8": "Trust company",
    "9": "Registered municipal advisor",
    "10": "Registered security-based swap dealer",
    "11": "Major security-based swap participant",
    "12": "Accountant or accounting firm",
    "13": "Lawyer or law firm",
    "14": "Other financial product salesperson",
}
for _n, _label in OTHER_BUSINESS.items():
    _add(f"6A({_n})", f"Also operates as: {_label}", "6", "Other business activities", "yn")
_add("6B(1)", "Actively engaged in another business not listed in Item 6.A.", "6", "Other business activities", "yn")
_add("6B(2)", "That other business is the firm's primary business", "6", "Other business activities", "yn")
_add("6B(3)", "Sells products or services other than investment advice to advisory clients", "6", "Other business activities", "yn")

# --------------------------------------------------------------------------
# Item 7 -- related persons (conflict-of-interest surface)
# --------------------------------------------------------------------------
RELATED_PERSONS = {
    "1": "Broker-dealer, municipal securities dealer, or government securities broker or dealer",
    "2": "Other investment adviser (including financial planners)",
    "3": "Registered municipal advisor",
    "4": "Registered security-based swap dealer",
    "5": "Major security-based swap participant",
    "6": "Commodity pool operator or commodity trading advisor",
    "7": "Futures commission merchant",
    "8": "Banking or thrift institution",
    "9": "Trust company",
    "10": "Accountant or accounting firm",
    "11": "Lawyer or law firm",
    "12": "Insurance company or agency",
    "13": "Pension consultant",
    "14": "Real estate broker or dealer",
    "15": "Sponsor or syndicator of limited partnerships",
    "16": "Sponsor, general partner, or managing member of pooled investment vehicles",
}
for _n, _label in RELATED_PERSONS.items():
    _add(f"7A({_n})", f"Has a related person that is a: {_label}", "7", "Affiliations", "yn")
_add("7B", "Private fund reporting (see Schedule D Section 7.B.)", "7", "Affiliations", "yn", verified=False, publish=False)

# --------------------------------------------------------------------------
# Item 8 -- participation or interest in client transactions
# --------------------------------------------------------------------------
_add("8A(1)", "Buys securities from or sells securities to advisory clients (principal transactions)", "8", "Conflicts of interest", "yn")
_add("8A(2)", "Buys or sells for itself securities it also recommends to clients", "8", "Conflicts of interest", "yn")
_add("8A(3)", "Recommends securities in which it has another proprietary interest", "8", "Conflicts of interest", "yn")
_add("8B(1)", "Executes agency cross transactions involving advisory client securities", "8", "Conflicts of interest", "yn")
_add("8B(2)", "Recommends securities for which it serves as underwriter or general partner", "8", "Conflicts of interest", "yn")
_add("8B(3)", "Recommends securities in which it has another sales interest", "8", "Conflicts of interest", "yn")
_add("8C(1)", "Has discretion over which securities are bought or sold", "8", "Discretion", "yn")
_add("8C(2)", "Has discretion over the amount of securities bought or sold", "8", "Discretion", "yn")
_add("8C(3)", "Has discretion over the broker or dealer used", "8", "Discretion", "yn")
_add("8C(4)", "Has discretion over commission rates paid", "8", "Discretion", "yn")
_add("8D", "Brokers or dealers selected under 8.C.(3) are related persons", "8", "Discretion", "yn")
_add("8E", "Recommends brokers or dealers to clients", "8", "Discretion", "yn")
_add("8F", "Brokers or dealers recommended under 8.E. are related persons", "8", "Discretion", "yn")
_add("8G(1)", "Receives soft dollar benefits", "8", "Conflicts of interest", "yn")
_add("8G(2)", "All soft dollar benefits are eligible under section 28(e)", "8", "Conflicts of interest", "yn")
_add("8H", "Compensates others for client referrals (legacy combined field)", "8", "Referral compensation", "yn")
_add("8H(1)", "Compensates a non-employee for client referrals", "8", "Referral compensation", "yn")
_add("8H(2)", "Provides employee compensation specifically for obtaining clients", "8", "Referral compensation", "yn")
_add("8I", "Receives compensation from others for client referrals", "8", "Referral compensation", "yn")

# --------------------------------------------------------------------------
# Item 9 -- custody
# --------------------------------------------------------------------------
_add("9A(1)(a)", "Has custody of client cash or bank accounts", "9", "Custody", "yn")
_add("9A(1)(b)", "Has custody of client securities", "9", "Custody", "yn")
_add("9A(2)(a)", "Client funds and securities held in the firm's custody", "9", "Custody", "money")
_add("9A(2)(b)", "Clients for whom the firm has custody", "9", "Custody", "int")
_add("9B(1)(a)", "A related person has custody of client cash or bank accounts", "9", "Custody", "yn")
_add("9B(1)(b)", "A related person has custody of client securities", "9", "Custody", "yn")
_add("9B(2)(a)", "Client funds and securities held in a related person's custody", "9", "Custody", "money")
_add("9B(2)(b)", "Clients for whom a related person has custody", "9", "Custody", "int")
_add("9C(1)", "A qualified custodian sends account statements at least quarterly to pooled vehicle investors", "9", "Custody controls", "yn")
_add("9C(2)", "An independent public accountant audits the pooled vehicles annually", "9", "Custody controls", "yn")
_add("9C(3)", "An independent public accountant conducts an annual surprise examination", "9", "Custody controls", "yn")
_add("9C(4)", "An independent public accountant prepares an internal control report", "9", "Custody controls", "yn")
_add("9D(1)", "The firm acts as a qualified custodian", "9", "Custody", "yn")
_add("9D(2)", "A related person acts as a qualified custodian", "9", "Custody", "yn")
_add("9E", "Date the most recent surprise examination commenced", "9", "Custody controls", "date")
_add("9F", "Number of persons acting as qualified custodians for the firm's clients", "9", "Custody", "int")

# --------------------------------------------------------------------------
# Item 10 -- control persons
# --------------------------------------------------------------------------
_add("10A", "A person not named in Item 1.A. or Schedules A/B controls the firm", "10", "Control", "yn")

# --------------------------------------------------------------------------
# Item 11 -- disciplinary disclosure.  Each flag has a paired "Count of X
# disclosures" column in the bulk file, handled by disclosure_count_column().
# --------------------------------------------------------------------------
DISCLOSURE_CATEGORIES = {
    "11A": ("Criminal", "Criminal Action DRP"),
    "11B": ("Criminal", "Criminal Action DRP"),
    "11C": ("Regulatory - SEC/CFTC", "Regulatory Action DRP"),
    "11D": ("Regulatory - other authority", "Regulatory Action DRP"),
    "11E": ("Self-regulatory organization", "Regulatory Action DRP"),
    "11F": ("Professional authorization", "Regulatory Action DRP"),
    "11G": ("Pending regulatory proceeding", "Regulatory Action DRP"),
    "11H": ("Civil judicial", "Civil Judicial Action DRP"),
}

_DISCLOSURES = {
    "11": "Any disclosure event involving the firm or its supervised persons",
    "11A(1)": "Convicted of or pled guilty/no contest to a felony (past 10 years)",
    "11A(2)": "Charged with a felony (past 10 years)",
    "11B(1)": "Convicted of or pled guilty/no contest to an investment-related misdemeanor (past 10 years)",
    "11B(2)": "Charged with an investment-related misdemeanor (past 10 years)",
    "11C(1)": "SEC or CFTC found a false statement or omission",
    "11C(2)": "SEC or CFTC found a violation of its regulations or statutes",
    "11C(3)": "SEC or CFTC found the firm caused a business to lose or have its authorization restricted",
    "11C(4)": "SEC or CFTC entered an order in connection with investment-related activity",
    "11C(5)": "SEC or CFTC imposed a civil money penalty or a cease-and-desist order",
    "11D(1)": "Another regulator found a false statement, omission, or dishonest, unfair, or unethical conduct",
    "11D(2)": "Another regulator found a violation of investment-related regulations or statutes",
    "11D(3)": "Another regulator found the firm caused a business to lose or have its authorization restricted",
    "11D(4)": "Another regulator entered an order in connection with investment-related activity (past 10 years)",
    "11D(5)": "Another regulator denied, suspended, or revoked a registration or restricted activity",
    "11E(1)": "A self-regulatory organization found a false statement or omission",
    "11E(2)": "A self-regulatory organization found a violation of its rules",
    "11E(3)": "A self-regulatory organization found the firm caused a business to lose or have its authorization restricted",
    "11E(4)": "A self-regulatory organization expelled, suspended, barred, or restricted the firm",
    "11F": "An authorization to act as an attorney, accountant, or federal contractor was revoked or suspended",
    "11G": "Currently subject to a regulatory proceeding that could result in a disclosure under 11.C., 11.D., or 11.E.",
    "11H(1)(a)": "A court enjoined the firm in connection with investment-related activity (past 10 years)",
    "11H(1)(b)": "A court found involvement in a violation of investment-related statutes or regulations",
    "11H(1)(c)": "A court dismissed an investment-related civil action under a settlement agreement",
    "11H(2)": "Currently subject to a civil proceeding that could result in a disclosure under 11.H.(1)",
}
for _code, _label in _DISCLOSURES.items():
    _prefix = _code[:3] if len(_code) > 2 else _code
    _group = DISCLOSURE_CATEGORIES.get(_prefix, ("Disclosure summary", ""))[0]
    _add(_code, _label, "11", _group, "yn")

# --------------------------------------------------------------------------
# Items not yet grounded in the form text.  Never published.
# --------------------------------------------------------------------------
for _code in ("1I", "1L", "1M", "1N", "1O", "1P"):
    _add(_code, f"Form ADV Item {_code} (not yet mapped)", "1", "Unmapped", "text", verified=False, publish=False)
for _n in list(range(1, 3)) + list(range(4, 14)):
    _add(f"2A({_n})", f"SEC registration eligibility basis {_n} (not yet mapped)", "2", "Unmapped", "yn", verified=False, publish=False)
for _code in ("12A", "12B(1)", "12B(2)", "12C(1)", "12C(2)"):
    _add(_code, f"Small business determination {_code} (not yet mapped)", "12", "Unmapped", "yn", verified=False, publish=False)

FIELDS: dict[str, Field] = dict(_FIELDS)


def disclosure_count_column(code: str) -> str:
    """Bulk-file column holding the event count paired with an Item 11 flag."""
    return f"Count of {code} disclosures"


def lookup(header: str) -> Field | None:
    """Resolve a raw CSV header to a Field, or None if it is already plain English."""
    return FIELDS.get(header.strip())


def label_for(header: str) -> str:
    """Human label for any header: mapped code, or the header itself."""
    field = lookup(header)
    return field.label if field else header.strip()


def publishable_codes() -> list[str]:
    return [code for code, f in FIELDS.items() if f.publish and f.verified]


def coverage() -> dict[str, int]:
    return {
        "total": len(FIELDS),
        "verified": sum(1 for f in FIELDS.values() if f.verified),
        "publishable": sum(1 for f in FIELDS.values() if f.publish and f.verified),
    }
