"""Synthetic PII builders so no PII-shaped literal lives in test source."""


def iban(n=1, spaced=False):
    digits = f"{n:02d}" + "0331" + f"{n:015d}"      # 2 + 19 digits
    s = "AE" + digits
    return " ".join([s[:4], s[4:8], s[8:12], s[12:16], s[16:20], s[20:]]) if spaced else s


def eid(n=1):
    return "-".join(["784", "1990", f"{n:07d}", str(n % 10)])


def pan(n=1):
    return "ABCDE" + f"{n:04d}" + "F"


def aadhaar(n=1):
    return " ".join(["2345", "6789", f"{n:04d}"])


def phone(n=1):
    return "+91 " + "98765" + " " + f"{n:05d}"


def email(n=1):
    return "user" + str(n) + "@" + "corp" + ".example"


def bank(n=1):
    return "A/C " + f"{n:011d}" + ", IFSC " + "ABCD0" + "123456"
