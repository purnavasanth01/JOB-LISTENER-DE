"""
Filter rules for the GERMAN listener (materials science students).

A job is kept when BOTH are true:
  1. it is a student job: Praktikum / Werkstudent / studentische Hilfskraft (HiWi) / Studentenjob
  2. it is about materials science (Werkstoffe, Metallografie, Polymere, Keramik, Beschichtung, ...)
     - a materials word in the TITLE is enough
     - otherwise the DESCRIPTION must contain at least 2 different materials words
Everything else is dropped with a reason (saved in dropped.csv so you can check the rules).
"""
import html
import re

# ------------------------------------------------------------------ student job type
# (label shown on the card, pattern). The first match wins.
KINDS = [
    ("Praktikum", re.compile(r"praktik|internship|\bintern\b|\bintern[:/ ]", re.I)),
    ("Werkstudent", re.compile(r"werkstudent|working[- ]student|student(?:in)?\s+worker", re.I)),
    ("HiWi", re.compile(r"\bhiwi\b|\bshk\b|\bwhk\b|studentische[rn]?\s+(?:hilfs|mitarbeit|aushilf|unterst)|"
                        r"wissenschaftliche[rn]?\s+hilfs|student(?:ische)?\s+(?:research\s+)?assistant|"
                        r"hilfskraft", re.I)),
    ("Studentenjob", re.compile(r"studentenjob|studentenjobs|nebenjob|studentische\s+tätigkeit|"
                                r"studentische\s+taetigkeit|aushilfe", re.I)),
]
# not what the friend wants: apprenticeships, dual study, trainee programs, PhD / postdoc, senior roles
EXCLUDE_TITLE = re.compile(r"ausbildung|auszubildende|\bazubi\b|duale[rn]?\s+student|duales\s+studium|\btrainee\b|"
                           r"apprentice|\bphd\b|doktorand|postdoc|promotion|\bsenior\b|berufserfahren|"
                           r"fachkraft|\bleiter(in)?\b|head of", re.I)
THESIS = re.compile(r"abschlussarbeit|masterarbeit|bachelorarbeit|diplomarbeit|\bthesis\b|studienarbeit", re.I)


def student_kind(title):
    """'Praktikum' | 'Werkstudent' | 'HiWi' | 'Studentenjob' | '' (not a student job)."""
    for label, pat in KINDS:
        if pat.search(title or ""):
            return label
    return ""


# ------------------------------------------------------------------ materials science words
# canonical name -> pattern. Counting DISTINCT names stops one repeated word from passing the description check.
TERMS = {
    "werkstoff": r"werkstoff",
    "materialwissenschaft": r"materialwissenschaft|materials?[ -]science|materialforschung|materials? research|"
                            r"materialkunde|werkstoffkunde",
    "material-lab": r"material(?:entwicklung|analyse|charakterisierung|prüfung|pruefung|technik|labor)|"
                    r"materials?[ -](?:development|analysis|characteri[sz]ation|testing|engineering|lab)|"
                    r"advanced materials|functional materials|werkstofflabor",
    "metallurgie": r"metallurg|metallograf|metallograph|materialograf|materialograph|gefüge|gefuege|"
                   r"microstructure|mikrostruktur",
    "korrosion": r"korrosion|corrosion",
    "tribologie": r"tribolog|verschleiß|verschleiss|\bwear\b",
    "polymer": r"polymer|kunststoff|elastomer|thermoplast|duroplast|thermoset|rubber|gummi|plastics?\b",
    "keramik": r"keramik|ceramic|glaskeramik|glass[- ]ceramic|\bglas\b|\bglass\b",
    "verbund": r"faserverbund|verbundwerkstoff|composites?\b|\bcfk\b|\bgfk\b|\bcfrp\b|\bgfrp\b|carbon fib",
    "beschichtung": r"beschichtung|coating|dünnschicht|duennschicht|thin[- ]film|galvan|electroplat|"
                    r"oberflächentechnik|oberflaechentechnik|surface (?:technology|engineering|analysis)|"
                    r"oberflächenanalyse|oberflaechenanalyse",
    "legierung": r"legierung|alloy|stahl\b|steel\b|hartmetall|pulvermetallurg|powder metallurgy|"
                 r"wärmebehandlung|waermebehandlung|heat treatment|gießerei|giesserei|casting",
    "analytik": r"rasterelektronen|\brem\b|\bsem\b|\btem\b|\bxrd\b|\bxps\b|\bfib\b|röntgen|roentgen|\bdsc\b|"
                r"\btga\b|\bftir\b|spectroscop|spektroskop|mikroskop|microscop",
    "schaden": r"schadensanalyse|schadens-? und|schadenanalyse|damage analysis|failure analysis|"
               r"werkstoffprüfung|werkstoffpruefung|materialprüfung|materialpruefung|zerstörungsfrei|"
               r"non-destructive|mechanische prüfung",
    "nano": r"nanomaterial|nanostruktur|nanotech|nanoparticle|nanopartikel",
    "elektrochemie": r"elektrochemi|electrochemi|batteriematerial|battery materials?|elektrodenmaterial|"
                     r"electrode materials?|kathoden|anoden|cathode|anode|solid[- ]state battery|festkörperbatterie",
    "halbleiter-material": r"halbleitermaterial|semiconductor materials?|wafer material|kristallzucht|crystal growth",
}
_TERMS = {k: re.compile(v, re.I) for k, v in TERMS.items()}


def clean_text(s):
    """HTML -> plain text."""
    s = html.unescape(s or "")
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def material_terms(text):
    """Sorted list of the distinct materials-science words found in text."""
    text = text or ""
    return sorted(k for k, pat in _TERMS.items() if pat.search(text))


# ------------------------------------------------------------------ decisions
def title_drop(title):
    """Reason to drop a job from its title alone, or None."""
    t = title or ""
    if EXCLUDE_TITLE.search(t):
        return "not a student job (apprenticeship / trainee / PhD / senior)"
    if not student_kind(t):
        return "not a student job"
    if THESIS.search(t) and not any(p.search(t) for k, p in KINDS if k in ("Praktikum", "Werkstudent")):
        return "thesis only (no internship / working-student wording)"
    return None


# Titles about other fields (purchasing, HR, IT, logistics ...). Dropped before any description is read, unless the
# title also says Werkstoff / Material (e.g. "Qualitätsmanagement Werkstoffprüfung" stays).
NON_FIELD = re.compile(r"einkauf|purchas|procurement|supply[- ]chain|logistik|logistic|human resources|\bhr\b|"
                       r"personal(?:wesen|entwicklung|marketing)|recruit|marketing|controlling|finance|finanz|"
                       r"buchhalt|vertrieb|\bsales\b|\blegal\b|\brecht\b|compliance|kommunikation|communication|"
                       r"consulting|projektmanagement|project management|software|full-?stack|embedded|\bki\b|\bai\b|"
                       r"data (?:science|analytics)|\bit-|cyber|digitali[sz]|innovation management|"
                       r"qualitätsmanagement|qualitaetsmanagement|quality management", re.I)
_MATERIAL_WORD = re.compile(r"werkstoff|material", re.I)


def other_field(title):
    t = title or ""
    return bool(NON_FIELD.search(t) and not _MATERIAL_WORD.search(t))


# ------------------------------------------------------------------ names and duplicates
COMPANY_STOP = {"gmbh", "ag", "se", "kg", "co", "mbh", "inc", "llc", "ltd", "the", "group", "gruppe", "holding"}
_GENDER = re.compile(r"\(\s*[a-z*]{1,3}\s*(?:[/|]\s*[a-z.*]{1,5}\s*){1,3}\)|\ball genders\b|\(gn\)|\*in\b|:in\b", re.I)


def norm_company(s):
    words = re.sub(r"[^a-z0-9äöüß ]+", " ", (s or "").lower().replace("&", " and ")).split()
    return "".join(w for w in words if w not in COMPANY_STOP)


_APP_STOP = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "plc", "lp", "group", "holdings",
             "technologies", "technology", "usa", "us", "na", "incorporated"}


def app_company_key(name):
    """Same normalisation as JOB_APP's normCompany(): the app stores blacklisted companies under this key."""
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower().replace("&", " and ")).split()
    return "".join(w for w in words if w not in _APP_STOP)


def title_key(title):
    """Same job posted for several locations -> same key."""
    t = _GENDER.sub(" ", (title or "").lower())
    return " ".join(re.sub(r"[^a-z0-9äöüß ]+", " ", t).split())
