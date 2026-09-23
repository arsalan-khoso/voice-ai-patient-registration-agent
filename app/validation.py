"""Field-level normalisers/validators shared by the REST API *and* the voice agent.

Every error message is written to be read aloud to a caller ("That date of birth is in the
future..."), so the voice agent can relay it verbatim and re-prompt for exactly that field.
"""
import re
import unicodedata
from datetime import date, datetime, timezone

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "U.S. Virgin Islands", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
_STATE_BY_NAME = {name.lower(): code for code, name in US_STATES.items()}

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
_SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male",
    "female": "Female", "f": "Female", "woman": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "declined": "Decline to Answer", "prefer not to say": "Decline to Answer",
    "prefer not to answer": "Decline to Answer", "rather not say": "Decline to Answer",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# letters, joined by single space / hyphen / apostrophe:  O'Brien, Mary-Jane, De La Cruz
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+$")
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")

MIN_BIRTH_YEAR = 1900


def clean_text(value: str) -> str:
    """Sanitise free text: unicode-normalise, drop control chars, collapse whitespace."""
    value = unicodedata.normalize("NFKC", value).replace("’", "'")
    return " ".join(_CONTROL_CHARS.sub(" ", value).split())


def _as_text(value, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"The {label} must be text.")
    return clean_text(str(value))


def _reject_markup(text: str, label: str) -> None:
    if "<" in text or ">" in text:
        raise ValueError(f"The {label} contains characters that aren't allowed.")


def validate_name(value, label: str) -> str:
    text = _as_text(value, label)
    if not text:
        raise ValueError(f"The {label} is required.")
    if len(text) > 50:
        raise ValueError(f"The {label} is too long - it must be 50 characters or fewer.")
    if not _NAME_RE.match(text):
        raise ValueError(f"The {label} can only contain letters, hyphens and apostrophes.")
    return text


def validate_text(value, label: str, *, max_len: int, required: bool = False) -> str | None:
    text = _as_text(value, label)
    if not text:
        if required:
            raise ValueError(f"The {label} is required.")
        return None
    if len(text) > max_len:
        raise ValueError(f"The {label} is too long - it must be {max_len} characters or fewer.")
    _reject_markup(text, label)
    return text


def parse_date_of_birth(value) -> date:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        parsed = value
    else:
        text = _as_text(value, "date of birth")
        if not text:
            raise ValueError("The date of birth is required.")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                "That isn't a valid date of birth. Please give the month, day and full four-digit year, "
                "for example 04/12/1985."
            )
    if parsed > datetime.now(timezone.utc).date():
        raise ValueError("That date of birth is in the future. Please give the actual date you were born.")
    if parsed.year < MIN_BIRTH_YEAR:
        raise ValueError("That year doesn't look right. Please give the four-digit year you were born.")
    return parsed


def normalize_phone(value, label: str = "phone number", *, required: bool = True) -> str | None:
    text = _as_text(value, label)
    if not text:
        if required:
            raise ValueError(f"The {label} is required.")
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(
            f"That {label} has {len(digits)} digits, but a U.S. phone number needs exactly 10 digits "
            "including the area code."
        )
    if digits[0] in "01":
        raise ValueError(f"That {label} doesn't look right - a U.S. area code can't start with 0 or 1.")
    return digits


def normalize_state(value) -> str:
    text = _as_text(value, "state")
    if not text:
        raise ValueError("The state is required.")
    code = text.upper().replace(".", "")
    if code in US_STATES:
        return code
    if text.lower() in _STATE_BY_NAME:
        return _STATE_BY_NAME[text.lower()]
    raise ValueError("That isn't a valid U.S. state. Please give the state name or its two-letter abbreviation.")


def normalize_zip(value) -> str:
    text = _as_text(value, "ZIP code").replace(" ", "")
    if not text:
        raise ValueError("The ZIP code is required.")
    if re.fullmatch(r"\d{9}", text):
        text = f"{text[:5]}-{text[5:]}"
    if not _ZIP_RE.match(text):
        raise ValueError("That ZIP code isn't valid. It needs to be 5 digits, or 9 digits for ZIP plus four.")
    return text


def normalize_email(value) -> str | None:
    text = _as_text(value, "email").replace(" ", "")
    if not text:
        return None
    if len(text) > 254 or not _EMAIL_RE.match(text):
        raise ValueError("That email address doesn't look valid. Please say it again, including the at sign and domain.")
    return text.lower()


def normalize_sex(value) -> str:
    text = _as_text(value, "sex").lower()
    if not text:
        raise ValueError("The sex is required.")
    if text in _SEX_ALIASES:
        return _SEX_ALIASES[text]
    raise ValueError("Sex must be one of: Male, Female, Other, or Decline to Answer.")


def normalize_member_id(value) -> str | None:
    text = _as_text(value, "insurance member ID").replace(" ", "")
    if not text:
        return None
    if len(text) > 50:
        raise ValueError("The insurance member ID is too long - it must be 50 characters or fewer.")
    if not _MEMBER_ID_RE.match(text):
        raise ValueError("The insurance member ID can only contain letters, numbers and hyphens.")
    return text.upper()


def normalize_language(value) -> str:
    text = validate_text(value, "preferred language", max_len=50)
    return text.title() if text else "English"


def mask_phone(phone: str | None) -> str | None:
    return None if not phone else "******" + phone[-4:]
