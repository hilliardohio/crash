#!/usr/bin/env python3
"""
oh1_ocr_extract.py — OCR extractor for SCANNED Ohio OH-1 crash reports (printed HSY 7001 OH-1 form,
scanned to image-only PDF). Text-layer PDFs are handed to oh1_extract.py automatically, so this is the
one script to run over a mixed folder.

    python3 oh1_ocr_extract.py <folder-or-pdf> [...] -o data [--merge] [--keep-report-number]
                               [--limit N] [--skip-done] [--workers N] [--debug-dir DIR]

Needs: pdftoppm (poppler), tesseract, python packages pdfplumber, pytesseract, Pillow, numpy.
Privacy: identical to oh1_extract.py — names, DOB, addresses, phones, plates, VIN, insurance, license,
citation numbers and officer details are never read into the output.
"""
import argparse, csv, datetime as dt, glob, json, os, re, shutil, subprocess, sys, tempfile, difflib
from collections import OrderedDict
import numpy as np
from PIL import Image, ImageOps
import pytesseract

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oh1_extract as T   # code tables + text-layer parser + finalize/write

DPI = 200
TESS_SPARSE = "--psm 11"

# ============================================================================ reference geometry
# All coordinates are pixels on a 200-dpi render (1700x2200) of the reference scan 2024-00000383.
ANCHORS = {
    "crash": [("LATITUDE", 1146, 372), ("NARRATIVE", 114, 1364), ("CONTOUR", 1124, 983), ("WEATHER", 684, 1173),
              ("DISPATCH", 490, 1924), ("SUPPLEMENT", 1426, 2027), ("HIT/SKIP", 1134, 231), ("REFERENCE", 505, 448),
              ("DAYLIGHT", 199, 1198), ("TRAFFICWAY", 276, 895)],
    "unit": [("OWNER", 126, 287), ("HAZARDOUS", 841, 548), ("AUTONOMOUS", 458, 890), ("SPECIAL", 125, 991),
             ("INITIAL", 1245, 1376), ("TRAFFICWAY", 1110, 1549), ("SEQUENCE", 123, 1701), ("DETECTED", 1395, 1940),
             ("HARMFUL", 265, 2106), ("DEFECTS", 127, 1223)],
    "motorist": [("ADDRESS", 143, 294), ("ADDRESS", 140, 1050), ("CONDITION", 981, 507), ("CONDITION", 981, 1261),
                 ("ENDORSEMENT", 223, 507), ("OFFENSE", 634, 434), ("GENDER", 1522, 223), ("CITATION", 1349, 1189),
                 ("ENDORSEMENT", 222, 1261), ("INJURIES", 137, 364)],
    "occupant": [("ADDRESS", 142, 300), ("CONTACT", 1097, 299), ("INJURIES", 138, 371), ("ADDRESS", 141, 519),
                 ("CONTACT", 1097, 954), ("BIRTH", 1262, 228), ("ADDRESS", 141, 1796), ("CONTACT", 1098, 2060)],
}

# field kinds: code = 1-2 digit box, comb = digit string, text = free text, cb = checkbox, narr = narrative
CRASH_FIELDS = {
    "report_number":   ("comb", (1090, 175, 1545, 220)),
    "hit_skip":        ("code", (1090, 250, 1135, 290)),
    "number_of_units": ("comb", (1265, 245, 1350, 292)),
    "unit_in_error":   ("comb", (1400, 245, 1465, 292)),
    "crash_dt":        ("comb", (1090, 320, 1345, 365)),
    "severity_code":   ("code", (1348, 322, 1400, 365)),
    "latitude":        ("comb", (1090, 392, 1345, 437)),
    "longitude":       ("comb", (1090, 470, 1345, 517)),
    "county":          ("comb", (115, 325, 190, 362)),
    "locality":        ("code", (210, 325, 265, 362)),
    "city":            ("text", (395, 320, 1085, 362)),
    "location_route_type":   ("text", (105, 395, 205, 435)),
    "location_route_number": ("comb", (210, 395, 355, 435)),
    "location_prefix": ("code", (360, 395, 400, 435)),
    "location_road_name":    ("text", (500, 395, 1000, 437)),
    "location_road_type":    ("text", (1000, 395, 1085, 437)),
    "reference_route_type":   ("text", (105, 475, 205, 515)),
    "reference_route_number": ("comb", (210, 475, 355, 515)),
    "reference_prefix": ("code", (360, 475, 400, 515)),
    "reference_road_name":    ("text", (500, 475, 1000, 517)),
    "reference_road_type":    ("text", (1000, 475, 1085, 517)),
    "reference_point": ("code", (112, 580, 160, 620)),
    "direction_from_reference": ("code", (303, 580, 355, 620)),
    "distance_from_reference":  ("comb", (120, 685, 280, 730)),
    "distance_unit":   ("code", (303, 688, 352, 730)),
    "within_intersection": ("cb", (1103, 555, 1135, 590)),
    "within_interchange":  ("cb", (1103, 600, 1135, 635)),
    "number_of_approaches": ("code", (1452, 575, 1512, 612)),
    "roadway_divided": ("cb", (1103, 690, 1135, 725)),
    "first_harmful_event_location": ("comb", (115, 790, 180, 835)),
    "manner_of_collision": ("code", (630, 790, 690, 835)),
    "direction_of_travel": ("code", (1098, 800, 1148, 842)),
    "median_type":     ("code", (1265, 800, 1318, 842)),
    "work_zone_related": ("cb", (120, 985, 150, 1010)),
    "workers_present": ("cb", (120, 1025, 150, 1050)),
    "law_enforcement_present": ("cb", (120, 1068, 150, 1093)),
    "active_school_zone": ("cb", (120, 1118, 150, 1145)),
    "work_zone_type":  ("code", (410, 1065, 460, 1095)),
    "contour":         ("code", (1130, 1010, 1190, 1050)),
    "road_condition":  ("code", (1300, 1010, 1360, 1050)),
    "surface":         ("code", (1480, 1010, 1545, 1050)),
    "light_condition": ("code", (115, 1215, 170, 1255)),
    "weather":         ("comb", (510, 1215, 570, 1255)),
    "narrative":       ("narr", (100, 1380, 1100, 1900)),
    "reported_dt":     ("comb", (100, 1945, 415, 1990)),
    "dispatch_dt":     ("comb", (420, 1945, 735, 1990)),
    "arrival_dt":      ("comb", (740, 1945, 1055, 1990)),
    "scene_cleared_dt": ("comb", (1060, 1945, 1375, 1990)),
    "police_agency":   ("cb", (1390, 1950, 1420, 1980)),
    "motorist_report": ("cb", (1390, 1985, 1420, 2015)),
    "supplement":      ("cb", (1390, 2025, 1420, 2055)),
    "roadway_closed_minutes": ("comb", (105, 2075, 250, 2115)),
    "other_investigation_minutes": ("comb", (260, 2075, 415, 2115)),
    "total_minutes":   ("comb", (425, 2075, 525, 2115)),
    "photos_taken":    ("cb", (118, 200, 145, 228)),
    "secondary_crash": ("cb", (118, 245, 145, 273)),
    "private_property": ("cb", (305, 255, 335, 283)),
}
UNIT_FIELDS = {
    "unit_number":     ("comb", (125, 250, 200, 288)),
    "damage_scale":    ("code", (1110, 285, 1175, 325)),
    "vehicle_year":    ("comb", (805, 405, 935, 445)),
    "vehicle_make":    ("text", (935, 405, 1085, 445)),
    "vehicle_color":   ("text", (810, 460, 930, 500)),
    "vehicle_model":   ("text", (935, 460, 1085, 500)),
    "insurance_verified": ("cb", (125, 455, 160, 485)),
    "type_of_use_commercial": ("cb", (130, 520, 160, 550)),
    "type_of_use_government": ("cb", (255, 520, 285, 550)),
    "in_emergency_response": ("cb", (375, 520, 405, 550)),
    "interlock_device": ("cb", (130, 580, 160, 610)),
    "hit_skip_unit":   ("cb", (255, 580, 285, 610)),
    "occupants":       ("comb", (415, 590, 495, 630)),
    "unit_type_code":  ("comb", (120, 665, 200, 710)),
    "trailing_units":  ("code", (125, 780, 200, 815)),
    "autonomous_code": ("code", (125, 860, 200, 900)),
    "special_function_code": ("comb", (125, 945, 200, 985)),
    "cargo_body_type_code": ("comb", (125, 1070, 200, 1110)),
    "vehicle_defects_code": ("comb", (125, 1180, 200, 1215)),
    "action_code":     ("code", (130, 1395, 200, 1435)),
    "pre_crash_code":  ("comb", (315, 1395, 385, 1435)),
    "initial_point_of_contact": ("comb", (1105, 1415, 1185, 1455)),
    "contributing_circumstance_code": ("comb", (130, 1590, 200, 1630)),
    "trafficway_flow_code": ("code", (1115, 1595, 1172, 1632)),
    "traffic_control_code": ("code", (1298, 1595, 1358, 1632)),
    "through_lanes":   ("code", (1140, 1700, 1210, 1740)),
    "rail_grade_crossing_code": ("code", (1300, 1700, 1360, 1740)),
    "seq1": ("comb", (142, 1740, 200, 1782)), "seq2": ("comb", (142, 1800, 200, 1842)), "seq3": ("comb", (142, 1858, 200, 1900)),
    "seq4": ("comb", (142, 1920, 200, 1962)), "seq5": ("comb", (142, 1982, 200, 2024)), "seq6": ("comb", (142, 2040, 200, 2082)),
    "direction_from_code": ("code", (1180, 1830, 1240, 1870)),
    "direction_to_code":   ("code", (1290, 1830, 1350, 1870)),
    "unit_speed":      ("comb", (1150, 1975, 1270, 2015)),
    "detected_speed_code": ("code", (1345, 1975, 1400, 2015)),
    "posted_speed":    ("comb", (1150, 2070, 1270, 2110)),
    "first_harmful_idx": ("code", (135, 2095, 195, 2130)),
    "most_harmful_idx":  ("code", (425, 2095, 485, 2130)),
}
MOTORIST_PITCH, MOTORIST_BLOCKS = 378, 3
MOTORIST_FIELDS = {   # block 1 coordinates; block k adds k*pitch to y
    "unit_number": ("comb", (140, 235, 215, 280)),
    "age":         ("comb", (1425, 240, 1500, 285)),
    "gender":      ("char", (1525, 240, 1588, 285)),
    "injury_code": ("code", (140, 385, 215, 425)),
    "injured_taken_by_code": ("code", (240, 390, 300, 425)),
    "ems_agency":  ("text", (315, 385, 620, 430)),
    "medical_facility": ("text", (630, 385, 955, 430)),
    "safety_equipment_code": ("comb", (1000, 395, 1080, 430)),
    "dot_helmet":  ("cb", (1092, 380, 1120, 408)),
    "seating_code": ("comb", (1235, 390, 1320, 425)),
    "air_bag_code": ("code", (1350, 390, 1430, 425)),
    "ejection_code": ("code", (1445, 390, 1510, 425)),
    "trapped_code": ("code", (1525, 390, 1590, 425)),
    "ol_state":    ("text", (135, 460, 215, 505)),
    "offense_charged": ("text", (630, 460, 890, 505)),
    "local_code":  ("cb", (905, 470, 940, 500)),
    "offense_description": ("text", (965, 460, 1340, 505)),
    "citation_number": ("text", (1345, 460, 1590, 505)),
    "ol_class_code": ("code", (140, 545, 215, 585)),
    "distracted_code": ("code", (600, 545, 680, 585)),
    "alcohol_suspected": ("cb", (700, 525, 730, 555)),
    "marijuana_suspected": ("cb", (830, 525, 860, 555)),
    "other_drug_suspected": ("cb", (700, 565, 730, 595)),
    "condition_code": ("code", (960, 545, 1080, 585)),
    "alcohol_test_status_code": ("code", (1090, 550, 1140, 585)),
    "alcohol_test_type_code": ("code", (1150, 550, 1200, 585)),
    "alcohol_test_value": ("comb", (1210, 550, 1320, 590)),
    "drug_test_status_code": ("code", (1330, 550, 1380, 585)),
    "drug_test_type_code": ("code", (1395, 550, 1445, 585)),
}
OCCUPANT_PITCH, OCCUPANT_BLOCKS = 219, 4
OCCUPANT_FIELDS = {
    "unit_number": ("comb", (140, 245, 215, 290)),
    "age":         ("comb", (1425, 250, 1500, 295)),
    "gender":      ("char", (1525, 250, 1588, 295)),
    "injury_code": ("code", (140, 390, 215, 430)),
    "injured_taken_by_code": ("code", (240, 395, 300, 430)),
    "ems_agency":  ("text", (315, 390, 620, 435)),
    "medical_facility": ("text", (630, 390, 955, 435)),
    "safety_equipment_code": ("comb", (1000, 395, 1080, 432)),
    "seating_code": ("comb", (1235, 390, 1320, 428)),
    "air_bag_code": ("code", (1350, 390, 1430, 428)),
    "ejection_code": ("code", (1445, 390, 1510, 428)),
    "trapped_code": ("code", (1525, 390, 1590, 428)),
}

# ============================================================================ OCR helpers
def tess_words(img, config):
    d = pytesseract.image_to_data(img, config=config, output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if not t: continue
        try: conf = float(d["conf"][i])
        except Exception: conf = -1
        out.append({"text": t, "x": d["left"][i], "y": d["top"][i], "w": d["width"][i], "h": d["height"][i], "conf": conf})
    return out

def _kind_from_header(txt):
    txt = txt.upper()
    if "CONTINUATION" in txt or "DIAGRAM" in txt:
        return "other"
    if "OCCUPANT" in txt or "WITNESS" in txt or "ADDENDUM" in txt: return "occupant"
    if "MOTORIST" in txt: return "motorist"
    if any(k in txt for k in ("PHOTOS", "SECONDARY", "MANDATORY", "SUPPLEMENT", "PRIVATE PROPERTY", "REPORTING AGENCY NAME")): return "crash"
    if "TRAFFIC" in txt and "CRASH" in txt: return "crash"
    if "UNIT" in txt and ("OWNER" in txt or "LOCAL" in txt or "DAMAGE" in txt): return "unit"
    return None

def page_kinds(imgs):
    """classify every page of a report with one tesseract run over the page headers"""
    heads = [im.crop((0, 60, 1150, 300)) for im in imgs]
    kinds = []
    for im, words in zip(imgs, _tess_run(heads, "--psm 6")):
        k = _kind_from_header(" ".join(w["text"] for w in words))
        if k is None:   # unreadable header: pick the page type whose anchor labels are found
            best, bestn = "other", 3
            for kind in ("unit", "motorist", "occupant", "crash"):
                n = len(find_anchors(im, kind))
                if n > bestn: best, bestn = kind, n
            k = best
        kinds.append(k)
    return kinds

def similar(a, b):
    a, b = a.upper().strip(".,:;*|"), b.upper()
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8

def find_anchors(img, kind):
    W, H = img.size
    wins, crops = [], []
    for text, rx, ry in ANCHORS[kind]:
        x0, y0 = max(0, rx - 150), max(0, ry - 65)
        x1, y1 = min(W, rx + 260), min(H, ry + 80)
        wins.append((text, rx, ry, x0, y0)); crops.append(img.crop((x0, y0, x1, y1)))
    pts = []
    for (text, rx, ry, x0, y0), words in zip(wins, _tess_run(crops, TESS_SPARSE)):
        best = None
        for w in words:
            if w["conf"] < 40 or not similar(w["text"], text): continue
            if best is None or w["conf"] > best["conf"]: best = w
        if best:
            pts.append(((rx, ry), (x0 + best["x"], y0 + best["y"])))
    return pts

def fit_transform(pts, scale_range=(0.9, 1.1)):
    """least-squares affine ref->scan; drops gross outliers; falls back to similarity / translation"""
    def solve(p):
        A = np.array([[rx, ry, 1] for (rx, ry), _ in p], float)
        bx = np.array([sx for _, (sx, sy) in p], float); by = np.array([sy for _, (sx, sy) in p], float)
        cx = np.linalg.lstsq(A, bx, rcond=None)[0]; cy = np.linalg.lstsq(A, by, rcond=None)[0]
        return np.vstack([cx, cy])
    if len(pts) == 0:
        sc = sum(scale_range) / 2 if scale_range[0] < 0.9 else 1.0
        return np.array([[sc, 0, 0], [0, sc, 0]], float), 0
    if len(pts) < 3:
        sc = sum(scale_range) / 2 if scale_range[0] < 0.9 else 1.0
        dx = np.mean([sx - sc * rx for (rx, ry), (sx, sy) in pts]); dy = np.mean([sy - sc * ry for (rx, ry), (sx, sy) in pts])
        return np.array([[sc, 0, dx], [0, sc, dy]], float), len(pts)
    p = list(pts)
    for _ in range(4):
        M = solve(p)
        res = [np.hypot(*(M @ np.array([rx, ry, 1]) - np.array([sx, sy]))) for (rx, ry), (sx, sy) in p]
        if max(res) < 18 or len(p) <= 3: break
        p.pop(int(np.argmax(res)))
    # sanity: scale must be close to 1 and rotation small; otherwise fall back to translation
    M = solve(p)
    lo, hi = scale_range
    if not (lo < M[0, 0] < hi and lo < M[1, 1] < hi and abs(M[0, 1]) < 0.08 * hi and abs(M[1, 0]) < 0.08 * hi):
        sc = (lo + hi) / 2 if lo < 0.9 else 1.0
        dx = np.median([sx - sc * rx for (rx, ry), (sx, sy) in p]); dy = np.median([sy - sc * ry for (rx, ry), (sx, sy) in p])
        M = np.array([[sc, 0, dx], [0, sc, dy]], float)
    return M, len(p)

def map_rect(M, r, pad=4):
    xs, ys = [], []
    for (x, y) in ((r[0], r[1]), (r[2], r[1]), (r[0], r[3]), (r[2], r[3])):
        sx, sy = M @ np.array([x, y, 1]); xs.append(sx); ys.append(sy)
    return (int(min(xs)) - pad, int(min(ys)) - pad, int(max(xs)) + pad, int(max(ys)) + pad)

def crop(img, box):
    W, H = img.size
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3])
    if x1 - x0 < 4 or y1 - y0 < 4: return Image.new("L", (10, 10), 255)
    return img.crop((x0, y0, x1, y1))

def binarize(im, thresh=150):
    return np.array(im) < thresh

def _erase_runs(a, axis_rows, maxlen, minlen=1, thin=False):
    """erase dark runs along rows (axis_rows=True) or columns longer than maxlen; with thin=True erase
    short runs (minlen..maxlen) in columns whose two neighbours on each side are blank – i.e. isolated 1-2px strokes"""
    h, w = a.shape
    if axis_rows:
        for y in range(h):
            row = a[y]; x = 0
            while x < w:
                if row[x]:
                    x2 = x
                    while x2 < w and row[x2]: x2 += 1
                    if x2 - x > maxlen: row[x:x2] = False
                    x = x2
                else: x += 1
    else:
        for x in range(w):
            col = a[:, x]; y = 0
            while y < h:
                if col[y]:
                    y2 = y
                    while y2 < h and col[y2]: y2 += 1
                    L = y2 - y
                    if (not thin and L > maxlen) or (thin and minlen <= L <= maxlen):
                        if thin:
                            lo, hi = max(0, x - 3), min(w, x + 4)
                            band = a[y:y2, lo:hi].copy(); band[:, max(0, x - 1 - lo):min(hi - lo, x + 2 - lo)] = False
                            if band.sum() <= 2: col[y:y2] = False
                        else:
                            col[y:y2] = False
                    y = y2
                else: y += 1

def clean_comb(im):
    """remove comb ticks, brackets/underlines, tiny specks and label leaks from a digit-field crop; returns PIL image"""
    a = binarize(im)
    h, w = a.shape
    _erase_runs(a, True, 22)                 # underlines / bracket bases
    _erase_runs(a, False, 34)                # vertical cell borders
    for (x0, y0, x1, y1) in components(a, 1):
        bh, bw = y1 - y0, x1 - x0
        pix = int(a[y0:y1, x0:x1].sum())
        drop = (bh < 11 and bw < 7)                 # tick remnants / specks
        drop |= (bw > 18 and bh < 15)               # bracket pieces
        drop |= pix < 10
        drop |= bh > 0.92 * h and bw < 6            # border fragments
        edge = (y0 <= 1 or x0 <= 1 or y1 >= h - 2 or x1 >= w - 2)
        drop |= edge and (bh < 11 or bh > 0.9 * h)  # label leaks (digit-sized glyphs at the edge are kept)
        if drop:
            a[y0:y1, x0:x1] &= False
    out = Image.fromarray(np.where(a, 0, 255).astype(np.uint8))
    return ImageOps.expand(out, border=12, fill=255)

def _runs(idx):
    runs = []
    for i in idx:
        if runs and i - runs[-1][1] <= 2: runs[-1][1] = i
        else: runs.append([i, i])
    return runs

def checkbox_checked(im):
    """locate the printed square (first two dark column runs + the matching row runs) and measure ink inside it"""
    a = binarize(im, 140)
    h, w = a.shape
    if h < 8 or w < 8: return False
    rruns = _runs(np.where(a.mean(axis=1) > 0.4)[0]); cruns = _runs(np.where(a.mean(axis=0) > 0.4)[0])
    box = None
    for i in range(len(cruns)):
        for j in range(i + 1, len(cruns)):
            side = cruns[j][1] - cruns[i][0]
            if 14 <= side <= 34:
                for p in range(len(rruns)):
                    for q in range(p + 1, len(rruns)):
                        vside = rruns[q][1] - rruns[p][0]
                        if abs(vside - side) <= 6:
                            box = (rruns[p][0], rruns[q][1], cruns[i][0], cruns[j][1]); break
                    if box: break
            if box: break
        if box: break
    if box is None:
        y0, y1, x0, x1 = int(h * .2), int(h * .8), int(w * .2), int(w * .8)
    else:
        y0, y1, x0, x1 = box
    my, mx = max(3, int((y1 - y0) * 0.3)), max(3, int((x1 - x0) * 0.3))
    inner = a[y0 + my:y1 - my + 1, x0 + mx:x1 - mx + 1]
    return inner.size > 0 and inner.mean() > 0.2

# ---------------------------------------------------------------- digit reader (template matching)
_TEMPLATES = None
def templates():
    """returns (labels list, matrix of mean-centred unit-norm template vectors)"""
    global _TEMPLATES
    if _TEMPLATES is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "digit_templates.json")) as f:
            raw = json.load(f)
        labels, rows = [], []
        for ch, ts in raw.items():
            for t in ts:
                v = np.array(t, float).ravel(); v = v - v.mean(); n = np.linalg.norm(v)
                if n == 0: continue
                labels.append(ch); rows.append(v / n)
        _TEMPLATES = (labels, np.vstack(rows))
    return _TEMPLATES

TW, TH = 16, 22
def norm_glyph(a):
    im = Image.fromarray((a * 255).astype(np.uint8)).resize((TW, TH), Image.BILINEAR)
    return np.array(im) / 255.0

def ncc(a, t):
    a = a - a.mean(); t = t - t.mean(); d = np.linalg.norm(a) * np.linalg.norm(t)
    return float((a * t).sum() / d) if d else 0.0

def components(a, min_pix=12):
    h, w = a.shape; lab = np.zeros_like(a, dtype=np.int32); n = 0; comps = []
    for y in range(h):
        for x in range(w):
            if a[y, x] and lab[y, x] == 0:
                n += 1; lab[y, x] = n; st = [(y, x)]; pix = []
                while st:
                    cy, cx = st.pop(); pix.append((cy, cx))
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < h and 0 <= nx < w and a[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = n; st.append((ny, nx))
                if len(pix) >= min_pix:
                    ys = [p[0] for p in pix]; xs = [p[1] for p in pix]
                    comps.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    comps.sort(key=lambda c: c[0])
    return comps

def classify_glyph(a, alphabet="0123456789"):
    labels, Mt = templates()
    g = norm_glyph(a).ravel(); g = g - g.mean(); n = np.linalg.norm(g)
    if n == 0: return 0.0, "?"
    sc = Mt @ (g / n)
    mask = np.array([ch in alphabet for ch in labels])
    sc = np.where(mask, sc, -1)
    i = int(np.argmax(sc))
    return float(sc[i]), labels[i]

def read_letter(im, alphabet="MF"):
    """single printed letter (gender) by template matching; '' when the cell is empty"""
    a = np.array(clean_comb(im)) < 128
    comps = [c for c in components(a) if 13 <= c[3] - c[1] <= 34 and 6 <= c[2] - c[0] <= 26]
    if not comps: return ""
    c = max(comps, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
    g = a[c[1]:c[3], c[0]:c[2]]
    sc, ch = classify_glyph(g, alphabet)
    if sc >= 0.45: return ch
    gi = ImageOps.expand(Image.fromarray(np.where(g, 0, 255).astype(np.uint8)), border=10, fill=255)
    gi = gi.resize((gi.size[0] * 3, gi.size[1] * 3))
    t = pytesseract.image_to_string(gi, config=f"--psm 10 -c tessedit_char_whitelist={alphabet}").strip().upper()
    return t[:1] if t[:1] in alphabet else ""

def clean_text(im):
    """erase cell borders / comb brackets from a free-text crop; returns (image, has_ink)"""
    a = binarize(im, 150); h, w = a.shape
    for y in range(h):
        row = a[y]; x = 0
        while x < w:
            if row[x]:
                x2 = x
                while x2 < w and row[x2]: x2 += 1
                if x2 - x > 40: row[x:x2] = False
                x = x2
            else: x += 1
    for x in range(w):
        col = a[:, x]; y = 0
        while y < h:
            if col[y]:
                y2 = y
                while y2 < h and col[y2]: y2 += 1
                if y2 - y > 28: col[y:y2] = False
                y = y2
            else: y += 1
    comps = components(a, 20)
    glyphs = [c for c in comps if 13 <= c[3] - c[1] <= 40 and 4 <= c[2] - c[0] <= 60]
    if glyphs:
        # keep the one text line that carries the biggest glyphs (labels of the next row bleed in at the bottom)
        big = sorted(glyphs, key=lambda c: -(c[3] - c[1]))[:max(1, len(glyphs) // 2)]
        cy = float(np.median([(c[1] + c[3]) / 2 for c in big]))
        keep = [c for c in comps if abs((c[1] + c[3]) / 2 - cy) <= 13]
        for c in comps:
            if c not in keep: a[c[1]:c[3], c[0]:c[2]] = False
        glyphs = [c for c in glyphs if c in keep]
    else:
        for c in comps: a[c[1]:c[3], c[0]:c[2]] = False
    out = ImageOps.expand(Image.fromarray(np.where(a, 0, 255).astype(np.uint8)), border=14, fill=255)
    return out, len(glyphs) >= 1

def tidy_text(s):
    s = re.sub(r"[^A-Za-z0-9 .,'&/()#;:-]", " ", s)
    toks = [t.strip(".,;:-_'") for t in s.split()]
    toks = [t for t in toks if re.search(r"[A-Za-z0-9]", t)]
    while toks and re.fullmatch(r"[A-Za-z]", toks[-1]) and toks[-1] not in ("A", "I"): toks.pop()
    while toks and re.fullmatch(r"[A-Za-z]", toks[0]) and toks[0] not in ("A", "I"): toks.pop(0)
    return " ".join(toks)

def glyph_boxes(a, single=False):
    """digit-sized glyph boxes (x0,y0,x1,y1) in a cleaned binary crop, left to right; touching digits split"""
    boxes = []
    comps = components(a)
    hs = [c[3] - c[1] for c in comps if c[2] - c[0] >= 6 and 11 <= c[3] - c[1] <= 36]
    med_h = float(np.median(hs)) if hs else 20.0
    for (x0, y0, x1, y1) in comps:
        if y1 - y0 > 1.15 * med_h:                   # taller than its neighbours: a comb tick is glued on
            while y1 - y0 > med_h and a[y1 - 1, x0:x1].sum() <= 3: y1 -= 1
            while y1 - y0 > med_h and a[y0, x0:x1].sum() <= 3: y0 += 1
        # a tick glued to a bottom corner: bottom rows whose little ink sits within 3px of a side edge
        n = 0
        while n < 10 and y1 - y0 > 11:
            row = a[y1 - 1, x0:x1]; ink = np.where(row)[0]
            if 0 < len(ink) <= 3 and (ink.max() < 3 or ink.min() >= (x1 - x0) - 3): y1 -= 1; n += 1
            else: break
        cols = np.where(a[y0:y1, x0:x1].sum(axis=0) > 0)[0]
        if len(cols) == 0: continue
        x0, x1 = x0 + int(cols[0]), x0 + int(cols[-1]) + 1
        w, h = x1 - x0, y1 - y0
        if h < 12 or h > 36 or w < 3: continue
        if w > 24:
            k = max(2, round(w / 15.5)); step = w / k
            for i in range(k):
                bx = (int(x0 + i * step), y0, int(x0 + (i + 1) * step), y1)
                if a[bx[1]:bx[3], bx[0]:bx[2]].sum() >= 25: boxes.append(bx)
        else:
            boxes.append((x0, y0, x1, y1))
    # comb ticks are as tall as a thin digit but sit lower than the digits: drop narrow glyphs below the text line
    wide = [b for b in boxes if b[2] - b[0] >= 6]
    if wide:
        cy = float(np.median([(b[1] + b[3]) / 2 for b in wide]))
        hh = float(np.median([b[3] - b[1] for b in wide]))
        boxes = [b for b in boxes if (b[3] - b[1]) >= 0.78 * hh and abs((b[1] + b[3]) / 2 - cy) <= 5]
    elif single:   # one-cell code box: a lone thin "1" is a digit when it sits in the middle of the cell
        W = a.shape[1]
        boxes = [b for b in boxes if b[2] - b[0] >= 4 or 0.25 * W < (b[0] + b[2]) / 2 < 0.75 * W]
    if single and boxes:   # label letters that leak into a code box are shorter than the digit
        hmax = max(b[3] - b[1] for b in boxes)
        boxes = [b for b in boxes if b[3] - b[1] >= 0.8 * hmax]
    else:
        boxes = [b for b in boxes if b[2] - b[0] >= 4]
    return boxes

def read_digits(im, single=False):
    """returns (digit string, min confidence, per-glyph scores); glyphs classified against the form digit fonts"""
    a = np.array(clean_comb(im)) < 128
    out, scores = "", []
    for (x0, y0, x1, y1) in glyph_boxes(a, single):
        g = a[y0:y1, x0:x1]
        sc, ch = classify_glyph(g, "0123456789/")
        if ch == "/": continue
        if sc < 0.3: continue                        # not a digit at all (bracket remnant, label letter, smudge)
        if sc < 0.45:
            gi = ImageOps.expand(Image.fromarray(np.where(g, 0, 255).astype(np.uint8)), border=10, fill=255)
            gi = gi.resize((gi.size[0] * 3, gi.size[1] * 3))
            t = pytesseract.image_to_string(gi, config="--psm 10 -c tessedit_char_whitelist=0123456789").strip()
            ch = t[:1] if t[:1].isdigit() else ""
            if not ch: continue
        out += ch; scores.append(sc)
    return out, (min(scores) if scores else 1.0), scores

def _tess_run(images, config):
    """run ONE tesseract process over several images; returns per-image word lists with boxes"""
    if not images: return []
    tmp = tempfile.mkdtemp(prefix="oh1b_")
    try:
        paths = []
        for i, im in enumerate(images):
            p = os.path.join(tmp, f"{i:03d}.png"); im.save(p); paths.append(p)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f: f.write("\n".join(paths))
        subprocess.run(["tesseract", lst, os.path.join(tmp, "out")] + config.split() + ["tsv"], capture_output=True, text=True)
        res = [[] for _ in images]
        with open(os.path.join(tmp, "out.tsv")) as f:
            for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                t = (row.get("text") or "").strip()
                if not t: continue
                try: conf = float(row.get("conf", -1))
                except Exception: conf = -1
                res[int(row["page_num"]) - 1].append({"text": t, "x": int(row["left"]), "y": int(row["top"]), "w": int(row["width"]), "h": int(row["height"]), "conf": conf})
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def tess_batch(images, config):
    """OCR several small images in ONE tesseract process; returns list of strings (same order)"""
    return [" ".join(w["text"] for w in words if w["conf"] > 20) for words in _tess_run(images, config)]

def ocr_fields(img, M, fields, debug=None):
    vals = {}
    text_items, char_items, narr_items = [], [], []
    for name, (kind, rect) in fields.items():
        im = crop(img, map_rect(M, rect, pad=5))
        if kind == "cb":
            vals[name] = checkbox_checked(im)
        elif kind in ("code", "comb"):
            s, conf, scores = read_digits(im, kind == "code")
            vals[name] = (int(s[:2]) if s else None) if kind == "code" else s
            vals["_conf_" + name] = conf; vals["_scores_" + name] = scores
        elif kind == "text":
            if name == "offense_charged":
                vals["_digits_" + name] = read_digits(im)[0]
            cim, ink = clean_text(im)
            if ink: text_items.append((name, cim))
            else: vals[name] = ""
        elif kind == "char":
            vals[name] = read_letter(im)
        elif kind == "narr":
            narr_items.append((name, im))
    for items, cfg in ((text_items, "--psm 7"), (narr_items, "--psm 6")):
        for (name, _), txt in zip(items, tess_batch([im for _, im in items], cfg)):
            vals[name] = tidy_text(txt) if cfg == "--psm 7" else " ".join(txt.split())
    return vals

# ============================================================================ value parsers
def codeval(v, table):
    """a code read from a single box: if the 2-digit read is not a valid code but its first digit is, a label leaked in"""
    if v is None: return None
    if v in table: return v
    if v >= 10 and (v // 10) in table: return v // 10
    if v >= 10 and (v % 10) in table and (v // 10) not in table: return v % 10
    return v

def comb_int(s, maxlen=2):
    d = re.sub(r"\D", "", s or "")
    return int(d[:maxlen]) if d else None

def comb_dt(s, year_hint=None):
    d = re.sub(r"\D", "", s or "")
    if len(d) >= 12:
        mo, day, yr, hh, mm = int(d[:2]), int(d[2:4]), int(d[4:8]), int(d[8:10]), int(d[10:12])
    elif len(d) == 11 and year_hint:        # one digit lost – try common cases
        cand = [d[:8] + d[8:], "0" + d]
        for c in cand:
            r = comb_dt(c, None)
            if r and (year_hint is None or r.year == year_hint): return r
        return None
    elif 8 <= len(d) < 12:
        mo, day, yr, hh, mm = int(d[:2]), int(d[2:4]), int(d[4:8]), 0, 0
        if len(d) >= 10: hh = int(d[8:10])
    else:
        return None
    if not (2000 <= yr <= 2100): return None
    try:
        return dt.datetime(yr, mo, day, min(hh, 23), min(mm, 59))
    except ValueError:
        return None

def comb_coord(s, intdigits, sign):
    d = re.sub(r"\D", "", s or "")
    if len(d) < intdigits + 3: return None
    v = float(d[:intdigits] + "." + d[intdigits:intdigits + 6])
    return sign * v

# ============================================================================ page sources
class ImageSource:
    """a rendered/scanned page: anchors by OCR, fields by OCR + template matching"""
    def __init__(self, img): self.img = img
    def anchors(self, kind): return find_anchors(self.img, kind)
    def read(self, M, fields, debug=None): return ocr_fields(self.img, M, fields, debug)
    def transform(self, kind):
        return fit_transform(self.anchors(kind))

class VectorSource:
    """a flattened fillable-form PDF page (values are real text): fields read straight from the text layer"""
    def __init__(self, words):
        self.words = words                      # pdfplumber words in PDF points
    def anchors(self, kind):
        pts = []
        for text, rx, ry in ANCHORS[kind]:
            cands = [w for w in self.words if similar(w["text"], text)]
            if not cands: continue
            # expected position in points ~ ref pixels * 0.356; choose the candidate nearest to it
            ex, ey = rx * 0.356, ry * 0.356
            w = min(cands, key=lambda w: (w["x0"] - ex) ** 2 + (w["top"] - ey) ** 2)
            if abs(w["x0"] - ex) < 60 and abs(w["top"] - ey) < 60:
                pts.append(((rx, ry), (w["x0"], w["top"])))
        return pts
    def transform(self, kind):
        pts = self.anchors(kind)
        M, n = fit_transform(pts, scale_range=(0.3, 0.42))
        return M, n
    def read(self, M, fields, debug=None):
        vals = {}
        for name, (kind, rect) in fields.items():
            x0, y0, x1, y1 = map_rect(M, rect, pad=1)
            inside = [w for w in self.words if x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and y0 <= (w["top"] + w["bottom"]) / 2 <= y1]
            inside.sort(key=lambda w: (round(w["top"] / 6), w["x0"]))
            txt = " ".join(w["text"] for w in inside)
            if kind == "cb":
                vals[name] = any(w["text"] in ("✘", "X", "x", "✓") for w in inside)
            elif kind == "code":
                d = re.sub(r"\D", "", txt)
                vals[name] = int(d[:2]) if d else None
                vals["_conf_" + name] = 1.0; vals["_scores_" + name] = [1.0] * len(d)
            elif kind == "comb":
                vals[name] = re.sub(r"[^0-9.\-]", "", txt)
                vals["_conf_" + name] = 1.0; vals["_scores_" + name] = [1.0] * len(vals[name])
            elif kind == "char":
                vals[name] = re.sub(r"[^A-Z]", "", txt.upper())[:1]
            elif kind == "narr":
                vals[name] = " ".join(txt.split())
            else:
                t = re.sub(r"\b(VEHICLE|MAKE|MODEL|COLOR|YEAR|OWNER|NAME|EMS|AGENCY|MEDICAL|FACILITY|OL|STATE|CITY|VILLAGE|TOWNSHIP|LOCATION|ROAD|TYPE)\b", " ", txt.replace("✘", ""))
                vals[name] = tidy_text(t) if name != "offense_charged" else " ".join(t.split())
        return vals

# ============================================================================ page parsers (scan)
def parse_crash_scan(src, debug):
    M, n = src.transform("crash")
    v = src.read(M, CRASH_FIELDS, debug)
    c = OrderedDict()
    c["report_number"] = re.sub(r"\D", "", v.get("report_number", ""))
    c["ncic"] = "02507"
    c["hit_skip"] = T.dec(T.HIT_SKIP, codeval(v.get("hit_skip"), T.HIT_SKIP))
    c["number_of_units"] = comb_int(v.get("number_of_units"))
    c["unit_in_error"] = comb_int(v.get("unit_in_error"))
    c["county"] = comb_int(v.get("county"))
    c["locality"] = T.dec(T.LOCALITY, codeval(v.get("locality"), T.LOCALITY))
    c["city"] = v.get("city", "")
    c["severity_code"] = v.get("severity_code")
    c["severity"] = T.dec(T.SEVERITY, c["severity_code"])
    c["_crash_dt"] = v.get("crash_dt", "")
    c["location_route_type"] = v.get("location_route_type", "")
    c["location_route_number"] = re.sub(r"\D", "", v.get("location_route_number", ""))
    c["location_prefix"] = None
    c["location_road_name"] = v.get("location_road_name", "")
    c["location_road_type"] = re.sub(r"[^A-Z]", "", v.get("location_road_type", "").upper())[:2]
    c["latitude"] = comb_coord(v.get("latitude"), 2, 1)
    c["reference_route_type"] = v.get("reference_route_type", "")
    c["reference_route_number"] = re.sub(r"\D", "", v.get("reference_route_number", ""))
    c["reference_prefix"] = None
    c["reference_road_name"] = v.get("reference_road_name", "")
    c["reference_road_type"] = re.sub(r"[^A-Z]", "", v.get("reference_road_type", "").upper())[:2]
    c["longitude"] = comb_coord(v.get("longitude"), 2, -1)
    c["reference_point"] = T.dec(T.REF_POINT, codeval(v.get("reference_point"), T.REF_POINT))
    c["direction_from_reference"] = T.dec(T.DIRECTION, codeval(v.get("direction_from_reference"), T.DIRECTION))
    c["distance_from_reference"] = comb_int(v.get("distance_from_reference"), 6)
    c["distance_unit"] = T.dec(T.DIST_UNIT, codeval(v.get("distance_unit"), T.DIST_UNIT))
    for k in ("within_intersection", "within_interchange", "roadway_divided", "work_zone_related", "workers_present",
              "law_enforcement_present", "active_school_zone", "supplement", "photos_taken", "secondary_crash", "private_property"):
        c[k] = bool(v.get(k))
    c["number_of_approaches"] = v.get("number_of_approaches")
    c["first_harmful_event_location"] = T.dec(T.FIRST_HARMFUL_LOC, codeval(comb_int(v.get("first_harmful_event_location")), T.FIRST_HARMFUL_LOC))
    c["manner_of_collision"] = T.dec(T.MANNER, codeval(v.get("manner_of_collision"), T.MANNER))
    c["direction_of_travel"] = T.dec(T.DIRECTION, codeval(v.get("direction_of_travel"), T.DIRECTION))
    c["median_type"] = T.dec(T.MEDIAN, codeval(v.get("median_type"), T.MEDIAN))
    c["work_zone_type"] = T.dec(T.WORK_ZONE_TYPE, codeval(v.get("work_zone_type"), T.WORK_ZONE_TYPE)) if c["work_zone_related"] else None
    c["contour"] = T.dec(T.CONTOUR, codeval(v.get("contour"), T.CONTOUR))
    c["road_condition"] = T.dec(T.ROAD_COND, codeval(v.get("road_condition"), T.ROAD_COND))
    c["surface"] = T.dec(T.SURFACE, codeval(v.get("surface"), T.SURFACE))
    c["light_condition"] = T.dec(T.LIGHT, codeval(v.get("light_condition"), T.LIGHT))
    c["weather"] = T.dec(T.WEATHER, codeval(comb_int(v.get("weather")), T.WEATHER))
    c["narrative"] = v.get("narrative", "")
    c["report_taken_by"] = "Police agency" if v.get("police_agency") else ("Motorist" if v.get("motorist_report") else None)
    c["roadway_closed_minutes"] = comb_int(v.get("roadway_closed_minutes"), 4)
    c["other_investigation_minutes"] = comb_int(v.get("other_investigation_minutes"), 4)
    c["total_minutes"] = comb_int(v.get("total_minutes"), 4)
    for k in ("reported_dt", "dispatch_dt", "arrival_dt", "scene_cleared_dt"):
        c["_" + k] = v.get(k, "")
    c["_anchors"] = n
    return c

def parse_unit_scan(src, debug):
    M, n = src.transform("unit")
    v = src.read(M, UNIT_FIELDS, debug)
    u = OrderedDict()
    u["unit_number"] = comb_int(v.get("unit_number"))
    u["damage_scale"] = T.dec(T.DAMAGE, codeval(v.get("damage_scale"), T.DAMAGE))
    yr = comb_int(v.get("vehicle_year"), 4)
    u["vehicle_year"] = yr if yr and 1900 < yr < 2100 else None
    u["vehicle_make"] = v.get("vehicle_make", "")
    u["vehicle_color"] = v.get("vehicle_color", "")
    u["vehicle_model"] = v.get("vehicle_model", "")
    for k in ("insurance_verified", "type_of_use_commercial", "type_of_use_government", "in_emergency_response", "interlock_device", "hit_skip_unit"):
        u[k] = bool(v.get(k))
    u["occupants"] = comb_int(v.get("occupants"))
    u["unit_type_code"] = comb_int(v.get("unit_type_code"))
    u["unit_type"] = T.dec(T.UNIT_TYPE, u["unit_type_code"])
    u["trailing_units"] = v.get("trailing_units")
    u["autonomous_vehicle"] = T.dec(T.AUTONOMOUS, codeval(v.get("autonomous_code"), T.AUTONOMOUS))
    u["special_function"] = T.dec(T.SPECIAL_FUNCTION, codeval(comb_int(v.get("special_function_code")), T.SPECIAL_FUNCTION))
    u["cargo_body_type_code"] = comb_int(v.get("cargo_body_type_code"))
    u["vehicle_defects_code"] = comb_int(v.get("vehicle_defects_code"))
    u["action"] = T.dec(T.ACTION, codeval(v.get("action_code"), T.ACTION))
    u["pre_crash_action"] = T.dec(T.PRE_CRASH, codeval(comb_int(v.get("pre_crash_code")), T.PRE_CRASH))
    u["initial_point_of_contact"] = comb_int(v.get("initial_point_of_contact"))
    u["contributing_circumstance_code"] = comb_int(v.get("contributing_circumstance_code"))
    u["contributing_circumstance"] = T.dec(T.CONTRIB, u["contributing_circumstance_code"])
    u["trafficway_flow"] = T.dec(T.TRAFFICWAY, codeval(v.get("trafficway_flow_code"), T.TRAFFICWAY))
    u["traffic_control"] = T.dec(T.TRAFFIC_CONTROL, codeval(v.get("traffic_control_code"), T.TRAFFIC_CONTROL))
    u["through_lanes"] = v.get("through_lanes")
    u["rail_grade_crossing"] = T.dec(T.RAIL_XING, codeval(v.get("rail_grade_crossing_code"), T.RAIL_XING))
    seq = []
    for k in ("seq1", "seq2", "seq3", "seq4", "seq5", "seq6"):
        n_ = comb_int(v.get(k))
        if n_: seq.append(T.dec(T.SEQ_EVENTS, n_))
    u["sequence_of_events"] = seq
    u["direction_from"] = T.dec(T.DIRECTION, codeval(v.get("direction_from_code"), T.DIRECTION))
    u["direction_to"] = T.dec(T.DIRECTION, codeval(v.get("direction_to_code"), T.DIRECTION))
    u["unit_speed"] = comb_int(v.get("unit_speed"), 3)
    u["detected_speed"] = T.dec(T.DETECTED_SPEED, codeval(v.get("detected_speed_code"), T.DETECTED_SPEED))
    u["posted_speed"] = comb_int(v.get("posted_speed"), 3)
    fh, mh = v.get("first_harmful_idx"), v.get("most_harmful_idx")
    u["first_harmful_event"] = seq[fh - 1] if fh and 1 <= fh <= len(seq) else None
    u["most_harmful_event"] = seq[mh - 1] if mh and 1 <= mh <= len(seq) else None
    u["_anchors"] = n
    return u

def parse_people_scan(src, kind, debug):
    M, n = src.transform(kind)
    base, pitch, nblocks = (MOTORIST_FIELDS, MOTORIST_PITCH, MOTORIST_BLOCKS) if kind == "motorist" else (OCCUPANT_FIELDS, OCCUPANT_PITCH, OCCUPANT_BLOCKS)
    people = []
    for b in range(nblocks):
        fields = {k: (kind_, (r[0], r[1] + b * pitch, r[2], r[3] + b * pitch)) for k, (kind_, r) in base.items()}
        v = src.read(M, fields, debug)
        unit = comb_int(v.get("unit_number")); age = comb_int(v.get("age"), 3)
        g = v.get("gender", "")
        inj = codeval(v.get("injury_code"), T.INJURY)
        if unit is None or not (1 <= unit <= 12): continue
        if inj not in (1, 2, 3, 4, 5) and not (age is not None and g): continue
        if age is not None and age > 105: age = None
        p = OrderedDict()
        p["role"] = "Driver / non-motorist" if kind == "motorist" else "Occupant / witness"
        p["unit_number"] = unit
        p["age"] = age if age is not None and age <= 110 else None
        p["gender"] = T.dec(T.GENDER, g[:1]) if g[:1] in ("M", "F", "U") else None
        p["injury_code"] = inj if inj in (1, 2, 3, 4, 5) else None
        p["injury"] = T.dec(T.INJURY, p["injury_code"])
        p["injured_taken_by"] = T.dec(T.INJURED_TAKEN_BY, codeval(v.get("injured_taken_by_code"), T.INJURED_TAKEN_BY))
        p["ems_agency"] = v.get("ems_agency", "")
        p["medical_facility"] = v.get("medical_facility", "")
        p["safety_equipment"] = T.dec(T.SAFETY_EQUIP, codeval(comb_int(v.get("safety_equipment_code")), T.SAFETY_EQUIP))
        p["dot_helmet"] = bool(v.get("dot_helmet"))
        p["seating_position"] = T.dec(T.SEATING, codeval(comb_int(v.get("seating_code")), T.SEATING))
        p["air_bag"] = T.dec(T.AIRBAG, codeval(v.get("air_bag_code"), T.AIRBAG))
        p["ejection"] = T.dec(T.EJECTION, codeval(v.get("ejection_code"), T.EJECTION))
        p["trapped"] = T.dec(T.TRAPPED, codeval(v.get("trapped_code"), T.TRAPPED))
        if kind == "motorist":
            p["ol_state"] = None
            oc = v.get("offense_charged", "")
            if not re.search(r"\d{3}", oc):
                digs = v.get("_digits_offense_charged", "")
                oc = oc if len(digs) < 3 else (oc + " " + digs).strip()
            p["offense_charged"] = oc
            p["local_code"] = bool(v.get("local_code"))
            p["offense_description"] = v.get("offense_description", "")
            cit = re.sub(r"\W", "", v.get("citation_number", ""))
            p["cited"] = bool(re.search(r"\d", p["offense_charged"])) or len(cit) >= 5
            p["ol_class"] = T.dec(T.OL_CLASS, codeval(v.get("ol_class_code"), T.OL_CLASS))
            p["distracted_by"] = T.dec(T.DISTRACTED, codeval(v.get("distracted_code"), T.DISTRACTED))
            p["alcohol_suspected"] = bool(v.get("alcohol_suspected"))
            p["marijuana_suspected"] = bool(v.get("marijuana_suspected"))
            p["other_drug_suspected"] = bool(v.get("other_drug_suspected"))
            p["condition"] = T.dec(T.CONDITION, codeval(v.get("condition_code"), T.CONDITION))
            p["alcohol_test_status"] = T.dec(T.TEST_STATUS, codeval(v.get("alcohol_test_status_code"), T.TEST_STATUS))
            p["alcohol_test_type"] = T.dec(T.ALCOHOL_TEST_TYPE, codeval(v.get("alcohol_test_type_code"), T.ALCOHOL_TEST_TYPE))
            d = re.sub(r"\D", "", v.get("alcohol_test_value", ""))
            p["alcohol_test_value"] = float("0." + d[:3]) if d and p["alcohol_test_status"] == "Test given, results known" else None
            p["drug_test_status"] = T.dec(T.TEST_STATUS, codeval(v.get("drug_test_status_code"), T.TEST_STATUS))
            p["drug_test_type"] = T.dec(T.DRUG_TEST_TYPE, codeval(v.get("drug_test_type_code"), T.DRUG_TEST_TYPE))
        people.append(p)
    return people

# ============================================================================ file driver
def render(pdf, tmp):
    subprocess.run(["pdftoppm", "-r", str(DPI), "-gray", pdf, os.path.join(tmp, "p")], check=True, capture_output=True)
    files = sorted(glob.glob(os.path.join(tmp, "p-*.pgm")), key=lambda f: int(re.search(r"-(\d+)\.pgm$", f).group(1)))
    return files

def normalize_page(img):
    """make every page 1700 wide, portrait; rotate landscape scans"""
    if img.size[0] > img.size[1]:
        img = img.rotate(90, expand=True)
    if img.size[0] != 1700:
        img = img.resize((1700, int(img.size[1] * 1700 / img.size[0])))
    return img

def has_text_layer(pdf):
    import pdfplumber
    try:
        with pdfplumber.open(pdf) as doc:
            return len(doc.pages[0].extract_words()) > 50
    except Exception:
        return False

def parse_scan(pdf, debug=None):
    tmp = tempfile.mkdtemp(prefix="oh1ocr_")
    try:
        crash, units, people = None, [], []
        imgs = [normalize_page(Image.open(f)) for f in render(pdf, tmp)]
        kinds = page_kinds(imgs)
        for img, k in zip(imgs, kinds):
            src = ImageSource(img)
            if k == "crash" and crash is None:
                crash = parse_crash_scan(src, debug)
            elif k == "unit":
                units.append(parse_unit_scan(src, debug))
            elif k in ("motorist", "occupant"):
                people += parse_people_scan(src, k, debug)
        if crash is None:
            raise ValueError(f"no crash page recognised (pages: {kinds})")
        for i, u in enumerate(units):                      # unit numbers that failed to read: use page order
            if not u.get("unit_number") or not (1 <= u["unit_number"] <= 12): u["unit_number"] = i + 1
        crash["units"] = units; crash["persons"] = people; crash["_pages"] = kinds
        return crash
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def parse_vector(pdf):
    """flattened fillable OH-1 (HSY 7001) with a real text layer, e.g. the iText exports"""
    import pdfplumber
    crash, units, people, kinds = None, [], [], []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            words = page.extract_words(x_tolerance=1, keep_blank_chars=False)
            txt = " ".join(w["text"] for w in words).upper()
            if "ADDENDUM" in txt or ("OCCUPANT" in txt and "WITNESS" in txt): k = "occupant"
            elif "OPERATOR LICENSE NUMBER" in txt or "OL CLASS" in txt: k = "motorist"
            elif "PHOTOS TAKEN" in txt or "SECONDARY CRASH" in txt: k = "crash"
            elif "OWNER NAME" in txt or "DAMAGE SCALE" in txt or "TRAILING UNITS" in txt: k = "unit"
            else: k = "other"
            kinds.append(k)
            src = VectorSource(words)
            if k == "crash" and crash is None: crash = parse_crash_scan(src, None)
            elif k == "unit": units.append(parse_unit_scan(src, None))
            elif k in ("motorist", "occupant"): people += parse_people_scan(src, k, None)
    if crash is None:
        raise ValueError(f"no crash page recognised (pages: {kinds})")
    for i, u in enumerate(units):
        if not u.get("unit_number") or not (1 <= u["unit_number"] <= 12): u["unit_number"] = i + 1
    crash["units"] = units; crash["persons"] = people; crash["_pages"] = kinds
    return crash

def report_number_from_name(path):
    b = os.path.basename(path)
    m = re.match(r"(\d{4})-?(\d{6,9})", b)
    if m:
        y, n = m.group(1), m.group(2)
        return f"{y}-{int(n):08d}"
    m = re.match(r"(\d{2})(\d{6})", b)      # 2020/2021 style 20002103 -> 2020-00002103
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):08d}"
    return None

def finalize_scan(c, path, keep):
    """turn raw scan fields into the same shape oh1_extract.finalize() produces"""
    rn = report_number_from_name(path) or c.get("report_number")
    year_hint = int(rn[:4]) if rn and rn[:4].isdigit() else None
    raw = c.pop("_crash_dt", "")
    d = comb_dt(raw, year_hint)
    exact = len(re.sub(r"\D", "", raw)) == 12
    # the reported/dispatch times repeat the date (and normally the time): use them when the crash field read is incomplete
    for k in ("_reported_dt", "_dispatch_dt", "_arrival_dt", "_scene_cleared_dt"):
        r2 = c.pop(k, "")
        alt = comb_dt(r2, year_hint)
        c[k.strip("_").replace("_dt", "")] = alt.strftime("%Y-%m-%dT%H:%M") if alt else None
        if alt is not None and k in ("_reported_dt", "_dispatch_dt") and len(re.sub(r"\D", "", r2)) == 12:
            if d is None or (not exact and abs((alt - d).total_seconds()) < 6 * 3600):
                d = alt; exact = True
    if d and year_hint and abs(d.year - year_hint) > 1:
        d = None
    c["crash_datetime_raw"] = d.strftime("%m/%d/%Y %H:%M") if d else ""
    c["report_number"] = rn or ""
    for k in ("reported", "dispatch", "arrival", "scene_cleared"):
        c[f"{k}_raw"] = dt.datetime.fromisoformat(c[k]).strftime("%m/%d/%Y %H:%M") if c.get(k) else ""
        c.pop(k, None)
    # plausibility for coordinates (Hilliard area)
    if c.get("latitude") is not None and not (39.8 < c["latitude"] < 40.3): c["latitude"] = None
    if c.get("longitude") is not None and not (-83.4 < c["longitude"] < -82.9): c["longitude"] = None
    c["source_type"] = "scan"
    out = T.finalize(c, keep)
    out["ocr_quality"] = {"crash_anchors": c.get("_anchors"), "pages": c.get("_pages")}
    for k in ("_anchors", "_pages"): out.pop(k, None)
    for u in out["units"]: u.pop("_anchors", None)
    return out

def process(path, keep, debug=None):
    c = None
    if has_text_layer(path):
        try:
            c = T.finalize(T.parse_pdf(path), keep)       # SSRS "CrashOH1M5" export with a text layer
            c["source_type"] = "text"
        except ValueError:
            try:
                c = finalize_scan(parse_vector(path), path, keep)   # flattened fillable form with a text layer
                c["source_type"] = "vector"
            except ValueError:
                c = None
    if c is None:
        c = finalize_scan(parse_scan(path, debug), path, keep)
    c["source_file"] = os.path.basename(path)
    return c

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", default="data")
    ap.add_argument("--keep-report-number", action="store_true")
    ap.add_argument("--merge", action="store_true", help="keep crashes already in <out>/crashes.json")
    ap.add_argument("--skip-done", action="store_true", help="skip PDFs whose source_file is already in <out>/crashes.json")
    ap.add_argument("--limit", type=int, default=0, help="stop after N newly parsed files (for chunked runs)")
    ap.add_argument("--time-budget", type=int, default=0, help="stop starting new files after this many seconds")
    ap.add_argument("--debug-dir", default=None)
    ap.add_argument("--raw-json", default=None, help="also dump every parsed crash (incl. failures) to this JSON file")
    args = ap.parse_args()

    files = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            for root, _, names in os.walk(inp):
                files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(".pdf") and not n.startswith(".")]
        elif inp.lower().endswith(".pdf"):
            files.append(inp)
    files.sort()
    existing, failures = {}, {}
    jpath = os.path.join(args.out, "crashes.json")
    if (args.merge or args.skip_done) and os.path.exists(jpath):
        with open(jpath) as f:
            blob = json.load(f)
        existing = {c["id"]: c for c in blob["crashes"]}
        failures = blob.get("failures", {})
    done_files = {c.get("source_file") for c in existing.values()} | set(failures)
    if args.debug_dir: os.makedirs(args.debug_dir, exist_ok=True)

    t0 = dt.datetime.now(); n_new = 0
    for path in files:
        if args.skip_done and os.path.basename(path) in done_files: continue
        if args.limit and n_new >= args.limit: break
        if args.time_budget and (dt.datetime.now() - t0).total_seconds() > args.time_budget: break
        t1 = dt.datetime.now()
        try:
            c = process(path, args.keep_report_number, args.debug_dir)
            existing[c["id"]] = c; failures.pop(os.path.basename(path), None)
            print(f"  ok   {os.path.basename(path):40s} {c.get('crash_date')} {c.get('hour')}h  {c.get('location_road')} / {c.get('reference_road')}  sev={c.get('severity_code')} lat={c.get('latitude')} [{c.get('source_type')}, {(dt.datetime.now()-t1).total_seconds():.0f}s]")
        except Exception as e:
            failures[os.path.basename(path)] = str(e)
            print(f"  FAIL {path}: {e}", file=sys.stderr)
        n_new += 1
    crashes = sorted(existing.values(), key=lambda c: c.get("crash_datetime") or "")
    os.makedirs(args.out, exist_ok=True)
    T.write_outputs(crashes, args.out)
    with open(jpath) as f: blob = json.load(f)
    blob["failures"] = failures
    with open(jpath, "w") as f: json.dump(blob, f, indent=1)
    print(f"\n{n_new} files processed this run; {len(crashes)} crashes total, {len(failures)} failures -> {args.out}/")

if __name__ == "__main__":
    main()
