#!/usr/bin/env python3
"""
oh1_extract.py — Extract crash data from Ohio OH-1 Traffic Crash Report PDFs
(the SSRS "CrashOH1M5" layout used by Hilliard PD) into dashboard-ready JSON/CSV.

Usage:
    python3 oh1_extract.py <folder-or-pdf> [more folders/pdfs...] [-o data] [--keep-report-number] [--merge]

  * Walks the given folders for *.pdf, parses each, and writes
        data/crashes.json   – one object per crash (units + persons nested), used by index.html
        data/crashes.csv    – one row per crash (flat)
        data/persons.csv    – one row per driver / occupant / non-motorist
  * Personally identifying information (names, DOB, addresses, phones, plates, VIN,
    insurance, license and citation numbers, officer names/badges) is never written.
  * By default the local report number is replaced by a short stable hash so the public
    data set cannot be joined back to a specific report; --keep-report-number keeps it.
  * --merge keeps crashes already in data/crashes.json that are not in the new batch
    (so you can drop new PDFs into a folder and re-run without re-parsing everything).

Requires:  pip install pdfplumber
"""
import argparse, csv, datetime as dt, hashlib, json, os, re, sys
from collections import OrderedDict

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber is required:  pip install pdfplumber")

# ----------------------------------------------------------------------------- code tables
SEVERITY = {1: "Fatal", 2: "Serious injury suspected", 3: "Minor injury suspected",
            4: "Injury possible", 5: "Property damage only"}
LIGHT = {1: "Daylight", 2: "Dawn/Dusk", 3: "Dark – lighted roadway", 4: "Dark – roadway not lighted",
         5: "Dark – unknown lighting", 9: "Other/Unknown"}
WEATHER = {1: "Clear", 2: "Cloudy", 3: "Fog/Smog/Smoke", 4: "Rain", 5: "Sleet/Hail", 6: "Snow",
           7: "Severe crosswinds", 8: "Blowing sand/soil/dirt/snow", 9: "Freezing rain/drizzle", 99: "Other/Unknown"}
SURFACE = {1: "Concrete", 2: "Blacktop/Asphalt", 3: "Brick/Block", 4: "Slag/Gravel/Stone", 5: "Dirt", 9: "Other/Unknown"}
ROAD_COND = {1: "Dry", 2: "Wet", 3: "Snow", 4: "Ice", 5: "Sand/Mud/Dirt/Oil/Gravel", 6: "Water (standing/moving)",
             7: "Slush", 9: "Other/Unknown"}
CONTOUR = {1: "Straight level", 2: "Straight grade", 3: "Curve level", 4: "Curve grade", 9: "Other/Unknown"}
MANNER = {1: "Not collision between two motor vehicles", 2: "Rear-end", 3: "Head-on", 4: "Rear-to-rear", 5: "Backing",
          6: "Angle", 7: "Sideswipe, same direction", 8: "Sideswipe, opposite direction", 9: "Other/Unknown"}
FIRST_HARMFUL_LOC = {1: "On roadway", 2: "On shoulder", 3: "In median", 4: "On roadside", 5: "On gore",
                     6: "Outside trafficway", 7: "On ramp", 8: "Off ramp", 9: "Crossover", 10: "Driveway/alley access",
                     11: "Railway grade crossing", 12: "Shared-use path/trail", 13: "Bike lane", 14: "Toll booth", 99: "Other/Unknown"}
DIRECTION = {1: "North", 2: "South", 3: "East", 4: "West", 5: "Northeast", 6: "Northwest", 7: "Southeast", 8: "Southwest", 9: "Other/Unknown"}
MEDIAN = {1: "Divided flush median (<4 ft)", 2: "Divided flush median (≥4 ft)", 3: "Divided, depressed median",
          4: "Divided, raised median", 9: "Other/Unknown"}
REF_POINT = {1: "Intersection", 2: "Mile post", 3: "House #"}
DIST_UNIT = {1: "Miles", 2: "Feet", 3: "Yards"}
WORK_ZONE_TYPE = {1: "Lane closure", 2: "Lane shift/crossover", 3: "Work on shoulder or median",
                  4: "Intermittent or moving work", 5: "Other"}
HIT_SKIP = {1: "Solved", 2: "Unsolved"}
LOCALITY = {1: "City", 2: "Village", 3: "Township"}
ROAD_TYPE = {"AL": "Alley", "AV": "Avenue", "BL": "Boulevard", "CR": "Circle", "CT": "Court", "DR": "Drive", "HE": "Heights",
             "HW": "Highway", "LA": "Lane", "MP": "Milepost", "OV": "Oval", "PK": "Parkway", "PI": "Pike", "PL": "Place",
             "RD": "Road", "SQ": "Square", "ST": "Street", "TE": "Terrace", "TL": "Trail", "WA": "Way"}

UNIT_TYPE = {1: "Passenger car", 2: "Passenger van (minivan)", 3: "Sport utility vehicle", 4: "Pick up", 5: "Cargo van",
             6: "Van (9-15 seats)", 7: "Motorcycle 2-wheeled", 8: "Motorcycle 3-wheeled", 9: "Autocycle",
             10: "Moped or motorized bicycle", 11: "All terrain vehicle", 12: "Golf cart", 13: "Snowmobile",
             14: "Single unit truck", 15: "Semi-tractor", 16: "Farm equipment", 17: "Motorhome", 18: "Limo",
             19: "Bus (16+ passengers)", 20: "Other vehicle", 21: "Heavy equipment", 22: "Animal with rider/animal-drawn",
             23: "Pedestrian/Skater", 24: "Wheelchair", 25: "Other non-motorist", 26: "Bicycle", 27: "Train", 99: "Unknown or hit/skip"}
DAMAGE = {1: "None", 2: "Minor damage", 3: "Functional damage", 4: "Disabling damage", 9: "Unknown"}
ACTION = {1: "Non-contact", 2: "Non-collision", 3: "Striking", 4: "Struck", 5: "Both striking & struck", 9: "Other/Unknown"}
PRE_CRASH = {1: "Straight ahead", 2: "Backing", 3: "Changing lanes", 4: "Overtaking/passing", 5: "Making right turn",
             6: "Making left turn", 7: "Making U-turn", 8: "Entering traffic lane", 9: "Leaving traffic lane", 10: "Parked",
             11: "Slowing or stopped in traffic", 12: "Driverless", 13: "Negotiating a curve", 14: "Entering or crossing specified location",
             15: "Walking, running, jogging, playing", 16: "Working", 17: "Pushing vehicle", 18: "Approaching or leaving vehicle",
             19: "Standing", 20: "Other non-motorist", 21: "Standing outside disabled vehicle", 99: "Other/Unknown"}
CONTRIB = {1: "None", 2: "Failure to yield", 3: "Ran red light", 4: "Ran stop sign", 5: "Unsafe speed", 6: "Improper turn",
           7: "Left of center", 8: "Following too close / ACDA", 9: "Improper lane change", 10: "Improper passing",
           11: "Drove off road", 12: "Improper backing", 13: "Improper start from parked position", 14: "Stopped or parked illegally",
           15: "Swerving to avoid", 16: "Wrong way", 17: "Vision obstruction", 18: "Operating defective equipment",
           19: "Load shifting/falling/spilling", 20: "Improper crossing", 21: "Lying in roadway", 22: "Not discernible",
           23: "Opening door into roadway", 99: "Other improper action"}
TRAFFICWAY = {1: "One-way", 2: "Two-way"}
TRAFFIC_CONTROL = {1: "Roundabout", 2: "Signal", 3: "Flasher", 4: "Stop sign", 5: "Yield sign", 6: "No control"}
RAIL_XING = {1: "Not involved", 2: "Involved – active crossing", 3: "Involved – passive crossing"}
SPECIAL_FUNCTION = {1: "None", 2: "Taxi", 3: "Electronic ride sharing", 4: "School transport", 5: "Bus – transit/commuter",
                    6: "Bus – charter/tour", 7: "Bus – intercity", 8: "Bus – shuttle", 9: "Bus – other", 10: "Ambulance",
                    11: "Fire", 12: "Military", 13: "Police", 14: "Public utility", 15: "Construction equipment",
                    16: "Farm", 17: "Mowing", 18: "Snow removal", 19: "Towing", 20: "Safety service patrol",
                    21: "Mail carrier", 99: "Other/Unknown"}
AUTONOMOUS = {1: "Yes", 2: "No", 9: "Other/Unknown"}
DETECTED_SPEED = {1: "Stated/estimated", 2: "Calculated/EDR", 3: "Undetermined"}
SEQ_EVENTS = {1: "Overturn/rollover", 2: "Fire/explosion", 3: "Immersion", 4: "Jackknife", 5: "Cargo/equipment loss or shift",
              6: "Equipment failure", 7: "Separation of units", 8: "Ran off road right", 9: "Ran off road left", 10: "Cross median",
              11: "Cross centerline – opposite direction of travel", 12: "Downhill runaway", 13: "Other non-collision",
              14: "Pedestrian", 15: "Pedalcycle", 16: "Railway vehicle", 17: "Animal – farm", 18: "Animal – deer",
              19: "Animal – other", 20: "Motor vehicle in transport", 21: "Parked motor vehicle", 22: "Work zone maintenance equipment",
              23: "Struck by falling, shifting cargo or anything set in motion by a motor vehicle", 24: "Other movable object",
              25: "Impact attenuator/crash cushion", 26: "Bridge overhead structure", 27: "Bridge pier or abutment",
              28: "Bridge parapet", 29: "Bridge rail", 30: "Guardrail face", 31: "Guardrail end", 32: "Portable barrier",
              33: "Median cable barrier", 34: "Median guardrail barrier", 35: "Median concrete barrier", 36: "Median other barrier",
              37: "Traffic sign post", 38: "Overhead sign post", 39: "Light/luminaries support", 40: "Utility pole",
              41: "Other post, pole or support", 42: "Culvert", 43: "Curb", 44: "Ditch", 45: "Embankment", 46: "Fence",
              47: "Mailbox", 48: "Tree", 49: "Fire hydrant", 50: "Work zone maintenance equipment", 51: "Wall", 52: "Building",
              53: "Tunnel", 54: "Other fixed object", 99: "Other/Unknown"}
INJURY = {1: "Fatal", 2: "Suspected serious injury", 3: "Suspected minor injury", 4: "Possible injury", 5: "No apparent injury"}
INJURED_TAKEN_BY = {1: "Not transported / treated at scene", 2: "EMS", 3: "Police", 9: "Other/Unknown"}
SAFETY_EQUIP = {1: "None used", 2: "Shoulder belt only", 3: "Lap belt only", 4: "Shoulder & lap belt", 5: "Child restraint – forward facing",
                6: "Child restraint – rear facing", 7: "Booster seat", 8: "Helmet", 9: "Protective pads", 10: "Reflective clothing",
                11: "Lighting – pedestrian/bicycle only", 99: "Other/Unknown"}
SEATING = {1: "Front – left (driver)", 2: "Front – middle", 3: "Front – right", 4: "Second – left", 5: "Second – middle",
           6: "Second – right", 7: "Third – left", 8: "Third – middle", 9: "Third – right", 10: "Sleeper section of truck cab",
           11: "Passenger in other enclosed cargo area", 12: "Passenger in unenclosed cargo area", 13: "Trailing unit",
           14: "Riding on vehicle exterior", 15: "Non-motorist", 99: "Other/Unknown"}
AIRBAG = {1: "Not deployed", 2: "Deployed front", 3: "Deployed side", 4: "Deployed both front/side", 5: "Not applicable", 9: "Deployment unknown"}
EJECTION = {1: "Not ejected", 2: "Partially ejected", 3: "Totally ejected", 4: "Not applicable"}
TRAPPED = {1: "Not trapped", 2: "Extricated by mechanical means", 3: "Freed by non-mechanical means"}
GENDER = {"M": "Male", "F": "Female", "U": "Other/Unknown"}
OL_CLASS = {1: "Class A", 2: "Class B", 3: "Class C", 4: "Regular class (Ohio = D)", 5: "M/C moped only", 6: "No valid OL"}
DISTRACTED = {1: "Not distracted", 2: "Manually operating an electronic communication device", 3: "Talking on hands-free device",
              4: "Talking on hand-held device", 5: "Other activity with an electronic device", 6: "Passenger",
              7: "Other distraction inside the vehicle", 8: "Other distraction outside the vehicle", 9: "Other/Unknown"}
CONDITION = {1: "Apparently normal", 2: "Physical impairment", 3: "Emotional (e.g., depressed, angry, disturbed)", 4: "Illness",
             5: "Fell asleep, fainted, fatigued", 6: "Under the influence of medications/drugs/alcohol", 9: "Other/Unknown"}
TEST_STATUS = {1: "None given", 2: "Test refused", 3: "Test given, contaminated sample/unusable", 4: "Test given, results known",
               5: "Test given, results unknown"}
ALCOHOL_TEST_TYPE = {1: "None", 2: "Blood", 3: "Urine", 4: "Breath", 5: "Other"}
DRUG_TEST_TYPE = {1: "None", 2: "Blood", 3: "Urine", 4: "Other"}
DRUG_RESULT = {1: "Amphetamines", 2: "Barbiturates", 3: "Benzodiazepines", 4: "Cannabinoids", 5: "Cocaine", 6: "Opiates/opioids",
               7: "Other", 8: "Negative results"}

# ----------------------------------------------------------------------------- geometry helpers
NUM = re.compile(r"^-?(\d+(\.\d+)?|\.\d+)$")

def page_words(page):
    return page.extract_words(x_tolerance=1.5, keep_blank_chars=False)

def classify(words):
    head = " ".join(w["text"] for w in words[:12])
    if "RAFFIC" in head and "RASH" in head: return "crash"
    if "CCUPANT" in head: return "occupant"
    if "OTORIST" in head: return "motorist"
    if head.startswith("U ") or " NIT " in head: return "unit"
    return "other"

def anchor(words, text, x_near, x_tol=10):
    for w in words:
        if w["text"] == text and abs(w["x0"] - x_near) < x_tol:
            return w
    return None

def shifted(words, ax, ay):
    out = []
    for w in words:
        out.append({"text": w["text"], "x0": w["x0"] - ax, "x1": w["x1"] - ax, "top": w["top"] - ay, "bottom": w["bottom"] - ay})
    return out

def in_box(ws, x0, x1, y0, y1):
    """words whose center lies inside the box, in reading order"""
    hits = [w for w in ws if x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and y0 <= (w["top"] + w["bottom"]) / 2 <= y1]
    hits.sort(key=lambda w: (round(w["top"] / 4), w["x0"]))
    return hits

def text(ws, x0, x1, y0, y1):
    return " ".join(w["text"] for w in in_box(ws, x0, x1, y0, y1)).strip()

def code(ws, x, y, w=14, h=9):
    """single coded value centred near (x,y); label numbers are followed by '-' so we exclude those"""
    for cand in in_box(ws, x - w, x + w, y - h, y + h):
        if NUM.match(cand["text"]):
            # ignore if it is the number of an option label ("1 - CITY") -> a '-' or '-WORD' token right after it
            nxt = [o for o in ws if abs(o["top"] - cand["top"]) < 3 and 0 < o["x0"] - cand["x1"] < 12]
            if nxt and nxt[0]["text"].startswith("-"):
                continue
            v = cand["text"]
            return float(v) if "." in v else int(v)
    return None

def checked(ws, label, lx, ly):
    """True when an 'X' sits in the checkbox immediately left of the label at (lx,ly)"""
    for w in ws:
        if w["text"] in ("X", "x") or w["text"].startswith("X("):
            if abs(w["x1"] - (lx - 3)) < 5 and abs(w["top"] - (ly - 1.3)) < 6:
                return True
    return False

def dec(table, v):
    if v is None: return None
    return table.get(int(v), f"Code {v}") if isinstance(v, (int, float)) else table.get(v, v)

def parse_dt(s):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})", s or "")
    if not m: return None
    mo, d, y, hh, mm = map(int, m.groups())
    try:
        return dt.datetime(y, mo, d, hh, mm)
    except ValueError:
        return None

# ----------------------------------------------------------------------------- page parsers
def parse_crash_page(words):
    a = anchor(words, "COUNTY*", 20)
    if not a: return None
    ws = shifted(words, a["x0"], a["top"])
    c = OrderedDict()
    c["report_number"] = text(ws, 420, 560, -47, -36).replace(" ", "")
    c["ncic"] = text(ws, 320, 360, -20, -8)
    c["hit_skip"] = dec(HIT_SKIP, code(ws, 397, -18))
    c["number_of_units"] = code(ws, 461, -16)
    c["unit_in_error"] = code(ws, 505, -16)
    c["county"] = code(ws, 11, 13)
    c["locality"] = dec(LOCALITY, code(ws, 43, 12))
    c["city"] = text(ws, 100, 380, 8, 20)
    c["crash_datetime_raw"] = text(ws, 390, 470, 10, 22)
    c["severity_code"] = code(ws, 486, 12)
    c["severity"] = dec(SEVERITY, c["severity_code"])
    # location (first road)
    c["location_route_type"] = text(ws, 6, 35, 36, 52)
    c["location_route_number"] = text(ws, 40, 95, 36, 52)
    c["location_prefix"] = dec(DIRECTION, code(ws, 118, 44, w=10))
    c["location_road_name"] = text(ws, 145, 345, 36, 52)
    rt = text(ws, 346, 380, 36, 52)
    c["location_road_type"] = rt
    c["latitude"] = code(ws, 430, 41, w=30)
    # reference road
    c["reference_route_type"] = text(ws, 6, 35, 66, 82)
    c["reference_route_number"] = text(ws, 40, 95, 66, 82)
    c["reference_prefix"] = dec(DIRECTION, code(ws, 118, 74, w=10))
    c["reference_road_name"] = text(ws, 145, 345, 66, 82)
    c["reference_road_type"] = text(ws, 346, 380, 66, 82)
    c["longitude"] = code(ws, 430, 73, w=30)
    c["reference_point"] = dec(REF_POINT, code(ws, 4, 107))
    c["direction_from_reference"] = dec(DIRECTION, code(ws, 83, 109))
    c["distance_from_reference"] = code(ws, 30, 155, w=25)
    c["distance_unit"] = dec(DIST_UNIT, code(ws, 81, 155))
    c["within_intersection"] = checked(ws, "WITHIN", 400.0, 101.6)
    c["within_interchange"] = checked(ws, "WITHIN", 400.0, 120.7)
    c["number_of_approaches"] = code(ws, 540, 135, w=20)
    c["roadway_divided"] = checked(ws, "ROADWAY", 400.0, 152.9)
    c["first_harmful_event_location"] = dec(FIRST_HARMFUL_LOC, code(ws, 10, 185))
    c["manner_of_collision"] = dec(MANNER, code(ws, 207, 185))
    c["direction_of_travel"] = dec(DIRECTION, code(ws, 390, 192))
    c["median_type"] = dec(MEDIAN, code(ws, 456, 192))
    c["work_zone_related"] = checked(ws, "WORK", 11.0, 260.6)
    c["workers_present"] = checked(ws, "WORKERS", 11.6, 276.0)
    c["law_enforcement_present"] = checked(ws, "LAW", 11.0, 291.5)
    c["active_school_zone"] = checked(ws, "ACTIVE", 12.0, 316.1)
    c["work_zone_type"] = dec(WORK_ZONE_TYPE, code(ws, 120, 268))
    c["contour"] = dec(CONTOUR, code(ws, 403, 269))
    c["road_condition"] = dec(ROAD_COND, code(ws, 469, 269))
    c["surface"] = dec(SURFACE, code(ws, 537, 269))
    c["light_condition"] = dec(LIGHT, code(ws, 7, 354))
    c["weather"] = dec(WEATHER, code(ws, 168, 353))
    c["narrative"] = text(ws, -5, 310, 412, 600)
    c["reported_raw"] = text(ws, 20, 95, 632, 644)
    c["dispatch_raw"] = text(ws, 145, 220, 632, 644)
    c["arrival_raw"] = text(ws, 270, 345, 632, 644)
    c["scene_cleared_raw"] = text(ws, 395, 470, 632, 644)
    c["report_taken_by"] = "Police agency" if checked(ws, "POLICE", 506.9, 634.6) else ("Motorist" if checked(ws, "MOTORIST", 506.9, 648.6) else None)
    c["supplement"] = checked(ws, "SUPPLEMENT", 505.9, 666.7)
    c["roadway_closed_minutes"] = code(ws, 22, 684, w=20)
    c["other_investigation_minutes"] = code(ws, 82, 684, w=20)
    c["total_minutes"] = code(ws, 138, 684, w=20)
    c["photos_taken"] = checked(ws, "PHOTOS", 12.2, -34.3)
    c["secondary_crash"] = checked(ws, "SECONDARY", 12.2, -18.5)
    c["private_property"] = checked(ws, "PRIVATE", 82.3, -12.5)
    return c

def parse_unit_page(words):
    a = anchor(words, "OWNER", 50)
    if not a: return None
    ws = shifted(words, a["x0"], a["top"])
    u = OrderedDict()
    u["unit_number"] = code(ws, -19, 9.4)
    u["damage_scale"] = dec(DAMAGE, code(ws, 364, 25.7))
    u["vehicle_year"] = code(ws, 251, 74.4, w=20)
    u["vehicle_make"] = text(ws, 285, 400, 69, 80)
    u["vehicle_color"] = text(ws, 230, 290, 89, 100)
    u["vehicle_model"] = text(ws, 290, 400, 89, 100)
    u["insurance_verified"] = checked(ws, "INSURANCE", -19.8, 87.2)
    u["type_of_use_commercial"] = checked(ws, "COMMERCIAL", -18.9, 116.7)
    u["type_of_use_government"] = checked(ws, "GOVERNMENT", 28.9, 116.7)
    u["in_emergency_response"] = checked(ws, "IN", 77.9, 112.8)
    u["interlock_device"] = checked(ws, "INTERLOCK", -19.0, 129.2)
    u["hit_skip_unit"] = checked(ws, "HIT/SKIP", 28.9, 135.9)
    u["vehicle_weight_class"] = {1: "≤10K lbs", 2: "10,001–26K lbs", 3: ">26K lbs"}.get(code(ws, 132, 136.4) or 0)
    u["occupants"] = code(ws, 97, 143.3)
    u["unit_type_code"] = code(ws, -17, 165.8)
    u["unit_type"] = dec(UNIT_TYPE, u["unit_type_code"])
    u["trailing_units"] = code(ws, -16, 209.6)
    u["autonomous_vehicle"] = dec(AUTONOMOUS, code(ws, -16, 244.4))
    u["special_function"] = dec(SPECIAL_FUNCTION, code(ws, -17, 278.1))
    u["cargo_body_type_code"] = code(ws, -16, 323.1)
    u["vehicle_defects_code"] = code(ws, -16, 362)
    u["non_motorist_location_code"] = code(ws, -16, 405)
    u["action"] = dec(ACTION, code(ws, -17, 453.8))
    u["pre_crash_action"] = dec(PRE_CRASH, code(ws, 60, 452.1))
    u["initial_point_of_contact"] = code(ws, 366, 457.3)
    u["contributing_circumstance_code"] = code(ws, -17, 527.2)
    u["contributing_circumstance"] = dec(CONTRIB, u["contributing_circumstance_code"])
    u["trafficway_flow"] = dec(TRAFFICWAY, code(ws, 363, 528.8))
    u["traffic_control"] = dec(TRAFFIC_CONTROL, code(ws, 433, 528.1))
    u["through_lanes"] = code(ws, 378, 570.3)
    u["rail_grade_crossing"] = dec(RAIL_XING, code(ws, 431, 569.9))
    seq = []
    for y in (581.9, 603.2, 625.3, 645.3, 666.3, 688.0):
        v = code(ws, -13, y, w=10)
        if v is not None: seq.append(dec(SEQ_EVENTS, v))
    u["sequence_of_events"] = seq
    u["direction_from"] = dec(DIRECTION, code(ws, 384, 624.2))
    u["direction_to"] = dec(DIRECTION, code(ws, 424, 624.2))
    u["unit_speed"] = code(ws, 386, 672.9, w=16)
    u["detected_speed"] = dec(DETECTED_SPEED, code(ws, 448, 687.2))
    u["posted_speed"] = code(ws, 386, 711.1, w=16)
    fh, mh = code(ws, -10, 712.8), code(ws, 106, 712.8)       # these are event numbers (1-6) into the sequence
    u["first_harmful_event"] = seq[fh - 1] if fh and 1 <= fh <= len(seq) else None
    u["most_harmful_event"] = seq[mh - 1] if mh and 1 <= mh <= len(seq) else None
    return u

def parse_person_blocks(words, kind):
    """motorist page has 3 blocks, occupant/witness page 4; each anchored on its ADDRESS: label"""
    anchors = [w for w in words if w["text"] == "ADDRESS:" and abs(w["x0"] - 20) < 10]
    people = []
    for a in anchors:
        ws = shifted(words, a["x0"], a["top"])
        unit = code(ws, 11, -12.5)
        age = code(ws, 512, -12.5, w=16)
        gender = text(ws, 538, 570, -18, -6)
        if unit is None and age is None and not gender:
            continue                                   # empty block
        p = OrderedDict()
        p["role"] = "Driver / non-motorist" if kind == "motorist" else "Occupant / witness"
        p["unit_number"] = unit
        p["age"] = age
        p["gender"] = dec(GENDER, gender or None)
        p["injury_code"] = code(ws, 10.5, 40.4)
        p["injury"] = dec(INJURY, p["injury_code"])
        p["injured_taken_by"] = dec(INJURED_TAKEN_BY, code(ws, 49, 41.2))
        p["ems_agency"] = text(ws, 60, 185, 30, 42)
        p["medical_facility"] = text(ws, 186, 335, 36, 46)
        p["safety_equipment"] = dec(SAFETY_EQUIP, code(ws, 341, 41.3))
        p["dot_helmet"] = checked(ws, "DOT-COMPLIANT", 382.3, 31.4)
        p["seating_position"] = dec(SEATING, code(ws, 442, 41.3))
        p["air_bag"] = dec(AIRBAG, code(ws, 485, 41.3))
        p["ejection"] = dec(EJECTION, code(ws, 519, 41.3))
        p["trapped"] = dec(TRAPPED, code(ws, 549, 41.8))
        if kind == "motorist":
            p["ol_state"] = text(ws, 0, 30, 64, 76)
            p["offense_charged"] = text(ws, 185, 295, 65, 76)
            p["local_code"] = any(w["text"] == "X" for w in in_box(ws, 296, 312, 66, 78))
            p["offense_description"] = text(ws, 315, 460, 65, 76)
            p["cited"] = bool(p["offense_charged"]) or bool(text(ws, 465, 560, 65, 76))
            p["ol_class"] = dec(OL_CLASS, code(ws, 10.5, 102.6))
            p["endorsement"] = text(ws, 30, 78, 96, 110)
            p["restriction"] = text(ws, 79, 150, 96, 110)
            p["distracted_by"] = dec(DISTRACTED, code(ws, 186, 104.2))
            p["alcohol_suspected"] = checked(ws, "ALCOHOL", 225.7, 94.9)
            p["marijuana_suspected"] = checked(ws, "MARIJUANA", 273.3, 94.9)
            p["other_drug_suspected"] = checked(ws, "OTHER", 225.7, 106.9)
            p["condition"] = dec(CONDITION, code(ws, 340, 102.6))
            p["alcohol_test_status"] = dec(TEST_STATUS, code(ws, 378, 105.3))
            p["alcohol_test_type"] = dec(ALCOHOL_TEST_TYPE, code(ws, 402, 105.3))
            p["alcohol_test_value"] = code(ws, 440, 105.3, w=25)
            p["drug_test_status"] = dec(TEST_STATUS, code(ws, 470, 105.3))
            p["drug_test_type"] = dec(DRUG_TEST_TYPE, code(ws, 497, 105.3))
            p["drug_test_results"] = [dec(DRUG_RESULT, int(w["text"])) for w in in_box(ws, 505, 575, 98, 112) if NUM.match(w["text"])]
        people.append(p)
    return people

# ----------------------------------------------------------------------------- per-file driver
def parse_pdf(path):
    crash = None; units = []; people = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page_words(page)
            kind = classify(words)
            if kind == "crash" and crash is None:
                crash = parse_crash_page(words)
            elif kind == "unit":
                u = parse_unit_page(words)
                if u: units.append(u)
            elif kind in ("motorist", "occupant"):
                people += parse_person_blocks(words, kind)
    if crash is None:
        raise ValueError("no crash page found")
    crash["units"] = units
    crash["persons"] = people
    return crash

def age_band(age):
    if age is None: return "Unknown"
    for lo, hi, lab in ((0, 15, "Under 16"), (16, 20, "16–20"), (21, 24, "21–24"), (25, 34, "25–34"), (35, 44, "35–44"),
                        (45, 54, "45–54"), (55, 64, "55–64"), (65, 74, "65–74"), (75, 200, "75+")):
        if lo <= age <= hi: return lab
    return "Unknown"

def finalize(c, keep_report_number):
    """derive dashboard fields; strip anything that must not be published"""
    d = parse_dt(c.pop("crash_datetime_raw", ""))
    rep = c.get("report_number") or ""
    c["id"] = rep if keep_report_number else hashlib.sha1(rep.encode()).hexdigest()[:10]
    if not keep_report_number:
        c.pop("report_number", None)
    c["crash_datetime"] = d.strftime("%Y-%m-%dT%H:%M") if d else None
    c["crash_date"] = d.strftime("%Y-%m-%d") if d else None
    c["year"] = d.year if d else None
    c["month"] = d.month if d else None
    c["hour"] = d.hour if d else None
    c["day_of_week"] = d.strftime("%A") if d else None
    for k in ("reported_raw", "dispatch_raw", "arrival_raw", "scene_cleared_raw"):
        v = parse_dt(c.pop(k, ""))
        c[k.replace("_raw", "")] = v.strftime("%Y-%m-%dT%H:%M") if v else None
    if c.get("arrival") and c.get("dispatch"):
        a, b = dt.datetime.fromisoformat(c["arrival"]), dt.datetime.fromisoformat(c["dispatch"])
        c["response_minutes"] = round((a - b).total_seconds() / 60, 1)
    rt = c.get("location_road_type")
    c["location_road"] = " ".join(x for x in (c.get("location_prefix"), c.get("location_road_name"), ROAD_TYPE.get(rt, rt)) if x).strip()
    rrt = c.get("reference_road_type")
    c["reference_road"] = " ".join(x for x in (c.get("reference_road_name"), ROAD_TYPE.get(rrt, rrt)) if x).strip()
    # driver-level rollups
    drivers = [p for p in c["persons"] if p["role"].startswith("Driver")]
    c["driver_ages"] = [p["age"] for p in drivers if p.get("age") is not None]
    c["driver_age_bands"] = [age_band(p.get("age")) for p in drivers]
    c["driver_genders"] = [p.get("gender") for p in drivers if p.get("gender")]
    uie = c.get("unit_in_error")
    err = next((p for p in drivers if p.get("unit_number") == uie), None)
    c["unit_in_error_driver_age"] = err.get("age") if err else None
    c["unit_in_error_driver_age_band"] = age_band(err.get("age")) if err else "Unknown"
    c["unit_in_error_driver_gender"] = err.get("gender") if err else None
    errunit = next((u for u in c["units"] if u.get("unit_number") == uie), None)
    c["primary_contributing_circumstance"] = errunit.get("contributing_circumstance") if errunit else None
    c["primary_unit_type"] = errunit.get("unit_type") if errunit else None
    c["alcohol_suspected"] = any(p.get("alcohol_suspected") for p in drivers)
    c["drug_suspected"] = any(p.get("marijuana_suspected") or p.get("other_drug_suspected") for p in drivers)
    c["distracted"] = any(p.get("distracted_by") not in (None, "Not distracted") for p in drivers)
    c["citation_issued"] = any(p.get("cited") for p in drivers)
    c["persons_count"] = len(c["persons"])
    c["injured_count"] = sum(1 for p in c["persons"] if p.get("injury_code") in (1, 2, 3, 4))
    c["fatal_count"] = sum(1 for p in c["persons"] if p.get("injury_code") == 1)
    c["unit_types"] = sorted({u.get("unit_type") for u in c["units"] if u.get("unit_type")})
    c["pedestrian_or_bicycle"] = any(u.get("unit_type_code") in (23, 24, 25, 26) for u in c["units"])
    c["motorcycle"] = any(u.get("unit_type_code") in (7, 8, 9, 10) for u in c["units"])
    for p in c["persons"]:
        p["age_band"] = age_band(p.get("age"))
    return c

FLAT_SKIP = {"units", "persons", "narrative"}

def write_outputs(crashes, outdir):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "crashes.json"), "w") as f:
        json.dump({"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "count": len(crashes), "crashes": crashes}, f, indent=1)
    cols = []
    for c in crashes:
        for k in c:
            if k not in FLAT_SKIP and k not in cols: cols.append(k)
    with open(os.path.join(outdir, "crashes.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for c in crashes:
            w.writerow({k: ("; ".join(map(str, v)) if isinstance(v, list) else v) for k, v in c.items()})
    pcols = ["crash_id", "crash_date", "hour"]
    rows = []
    for c in crashes:
        for p in c["persons"]:
            r = {"crash_id": c["id"], "crash_date": c["crash_date"], "hour": c["hour"]}
            r.update({k: ("; ".join(map(str, v)) if isinstance(v, list) else v) for k, v in p.items()})
            rows.append(r)
            for k in p:
                if k not in pcols: pcols.append(k)
    with open(os.path.join(outdir, "persons.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=pcols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="PDF files or folders of OH-1 PDFs")
    ap.add_argument("-o", "--out", default="data", help="output folder (default: data)")
    ap.add_argument("--keep-report-number", action="store_true", help="publish the local report number instead of a hash")
    ap.add_argument("--merge", action="store_true", help="keep crashes already in <out>/crashes.json")
    args = ap.parse_args()

    files = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            for root, _, names in os.walk(inp):
                files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith(".pdf")]
        elif inp.lower().endswith(".pdf"):
            files.append(inp)
    if not files:
        sys.exit("no PDF files found")

    existing = {}
    if args.merge:
        try:
            with open(os.path.join(args.out, "crashes.json")) as f:
                existing = {c["id"]: c for c in json.load(f)["crashes"]}
        except FileNotFoundError:
            pass

    ok = 0
    for path in files:
        try:
            c = finalize(parse_pdf(path), args.keep_report_number)
            c["source_file"] = os.path.basename(path)
            existing[c["id"]] = c
            ok += 1
            print(f"  parsed  {os.path.basename(path)}  ->  {c.get('crash_date')} {c.get('location_road')} / {c.get('reference_road')}  [{c.get('severity')}]")
        except Exception as e:  # keep going on a bad file
            print(f"  FAILED  {path}: {e}", file=sys.stderr)

    crashes = sorted(existing.values(), key=lambda c: c.get("crash_datetime") or "")
    write_outputs(crashes, args.out)
    print(f"\n{ok} of {len(files)} PDFs parsed; {len(crashes)} crashes written to {args.out}/")

if __name__ == "__main__":
    main()
