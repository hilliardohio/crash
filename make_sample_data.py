#!/usr/bin/env python3
"""
make_sample_data.py — writes data/sample.json: ~400 SYNTHETIC crashes shaped exactly like
oh1_extract.py output, so the dashboard can be previewed before real reports are loaded.
Locations are jittered around real Hilliard intersections; every other value is random.
The file is flagged "synthetic": true and the dashboard shows a banner when it is loaded.
"""
import json, random, datetime as dt, os, hashlib
random.seed(26)

SPOTS = [  # (location road, reference road, lat, lon, weight)
    ("Cemetery Road", "Main Street", 40.0334, -83.1583, 6),
    ("Cemetery Road", "Hilliard-Rome Road", 40.0343, -83.1254, 7),
    ("Cemetery Road", "I-270 ramp", 40.0336, -83.1215, 5),
    ("Cemetery Road", "Lyman Drive", 40.0333, -83.1228, 4),
    ("Scioto Darby Road", "Main Street", 40.0299, -83.1575, 4),
    ("Scioto Darby Road", "Cosgray Road", 40.0287, -83.1889, 3),
    ("Scioto Darby Road", "Leppert Road", 40.0296, -83.1744, 3),
    ("Davidson Road", "Leap Road", 40.0208, -83.1420, 5),
    ("Davidson Road", "Britton Parkway", 40.0213, -83.1327, 6),
    ("Davidson Road", "Hilliard-Rome Road", 40.0210, -83.1262, 5),
    ("Fishinger Boulevard", "Hilliard-Rome Road", 40.0249, -83.1263, 5),
    ("Fishinger Boulevard", "Mill Run Drive", 40.0255, -83.1130, 3),
    ("Roberts Road", "Hilliard-Rome Road", 40.0525, -83.1248, 5),
    ("Roberts Road", "Walker Road", 40.0527, -83.1443, 2),
    ("Main Street", "Norwich Street", 40.0355, -83.1580, 2),
    ("Main Street", "Hayden Run Road", 40.0458, -83.1545, 3),
    ("Leap Road", "Hayden Run Road", 40.0414, -83.1372, 3),
    ("Britton Parkway", "Trueman Boulevard", 40.0272, -83.1322, 3),
    ("Alton Darby Creek Road", "Scioto Darby Road", 40.0292, -83.2045, 2),
    ("Dublin Road", "Hayden Run Road", 40.0440, -83.1175, 2),
    ("Cosgray Road", "Hayden Run Road", 40.0412, -83.1892, 2),
    ("Avery Road", "Cemetery Road", 40.0354, -83.1707, 3),
]
SEV = [(5, "Property damage only", 0.70), (4, "Injury possible", 0.13), (3, "Minor injury suspected", 0.11),
       (2, "Serious injury suspected", 0.05), (1, "Fatal", 0.01)]
LIGHT = ["Daylight"] * 7 + ["Dark – lighted roadway"] * 2 + ["Dawn/Dusk", "Dark – roadway not lighted"]
WEATHER = ["Clear"] * 6 + ["Cloudy"] * 3 + ["Rain", "Snow"]
ROADCOND = {"Clear": ["Dry"] * 9 + ["Wet"], "Cloudy": ["Dry"] * 7 + ["Wet"] * 3, "Rain": ["Wet"], "Snow": ["Snow", "Ice", "Slush"]}
MANNER = ["Rear-end"] * 4 + ["Angle"] * 3 + ["Sideswipe, same direction"] * 2 + ["Not collision between two motor vehicles", "Backing", "Head-on"]
CONTRIB = ["Following too close / ACDA"] * 4 + ["Failure to yield"] * 3 + ["Improper lane change", "Unsafe speed", "Ran red light",
           "Drove off road", "Improper backing", "Improper turn", "Other improper action", "None"]
UNIT_TYPES = ["Passenger car"] * 5 + ["Sport utility vehicle"] * 4 + ["Pick up"] * 2 + ["Passenger van (minivan)", "Single unit truck",
              "Motorcycle 2-wheeled", "Semi-tractor"]
DISTRACT = ["Not distracted"] * 8 + ["Manually operating an electronic communication device", "Passenger", "Other distraction inside the vehicle"]
DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def age_band(age):
    for lo, hi, lab in ((0, 15, "Under 16"), (16, 20, "16–20"), (21, 24, "21–24"), (25, 34, "25–34"), (35, 44, "35–44"),
                        (45, 54, "45–54"), (55, 64, "55–64"), (65, 74, "65–74"), (75, 200, "75+")):
        if lo <= age <= hi: return lab
    return "Unknown"

def rand_age():
    return int(min(94, max(16, random.choice([random.gauss(34, 14), random.gauss(45, 18)]))))

def rand_hour():
    r = random.random()
    if r < 0.18: return random.choice([7, 8, 8, 9])
    if r < 0.60: return random.choice([15, 16, 16, 17, 17, 18])
    if r < 0.85: return random.randint(10, 14)
    return random.choice([0, 1, 2, 3, 5, 6, 19, 20, 21, 22, 23])

crashes = []
start = dt.date(2025, 1, 1)
for i in range(420):
    d = start + dt.timedelta(days=random.randint(0, 665))
    h = rand_hour()
    when = dt.datetime(d.year, d.month, d.day, h, random.randint(0, 59))
    spot = random.choices(SPOTS, weights=[s[4] for s in SPOTS])[0]
    lat = spot[2] + random.gauss(0, 0.0006); lon = spot[3] + random.gauss(0, 0.0008)
    sev = random.choices(SEV, weights=[s[2] for s in SEV])[0]
    light = "Daylight" if 7 <= h <= 18 else random.choice(["Dark – lighted roadway", "Dark – lighted roadway", "Dark – roadway not lighted", "Dawn/Dusk"])
    if d.month in (11, 12, 1, 2): weather = random.choice(WEATHER + ["Snow", "Snow", "Rain"])
    else: weather = random.choice(WEATHER)
    manner = random.choice(MANNER)
    nunits = 1 if manner == "Not collision between two motor vehicles" else random.choice([2, 2, 2, 2, 3])
    units, persons = [], []
    for u in range(1, nunits + 1):
        ut = random.choice(UNIT_TYPES)
        age = rand_age()
        gender = random.choice(["Male", "Male", "Female", "Female", "Female"])
        units.append({"unit_number": u, "unit_type": ut, "damage_scale": random.choice(["Minor damage", "Functional damage", "Disabling damage"]),
                      "contributing_circumstance": random.choice(CONTRIB) if u == 1 else "None",
                      "pre_crash_action": random.choice(["Straight ahead", "Slowing or stopped in traffic", "Making left turn", "Changing lanes"]),
                      "occupants": random.choice([1, 1, 1, 2, 2, 3]), "posted_speed": spot and random.choice([25, 35, 35, 45]),
                      "vehicle_year": random.randint(2005, 2026), "sequence_of_events": ["Motor vehicle in transport"]})
        inj = 5
        if sev[0] <= 4 and random.random() < 0.6: inj = max(sev[0], random.choice([3, 4, 4, 5]))
        drv = {"role": "Driver / non-motorist", "unit_number": u, "age": age, "age_band": age_band(age), "gender": gender,
               "injury_code": inj, "injury": {1: "Fatal", 2: "Suspected serious injury", 3: "Suspected minor injury", 4: "Possible injury", 5: "No apparent injury"}[inj],
               "safety_equipment": random.choice(["Shoulder & lap belt"] * 9 + ["None used"]), "seating_position": "Front – left (driver)",
               "distracted_by": random.choice(DISTRACT), "alcohol_suspected": random.random() < (0.12 if h < 5 or h > 21 else 0.02),
               "marijuana_suspected": random.random() < 0.02, "other_drug_suspected": False, "cited": u == 1 and random.random() < 0.55}
        persons.append(drv)
        for o in range(units[-1]["occupants"] - 1):
            oa = rand_age() if random.random() < 0.7 else random.randint(2, 15)
            persons.append({"role": "Occupant / witness", "unit_number": u, "age": oa, "age_band": age_band(oa),
                            "gender": random.choice(["Male", "Female"]), "injury_code": 5, "injury": "No apparent injury",
                            "safety_equipment": "Shoulder & lap belt"})
    if sev[0] == 1:
        persons[0]["injury_code"] = 1; persons[0]["injury"] = "Fatal"
    drivers = [p for p in persons if p["role"].startswith("Driver")]
    err = drivers[0]
    ped = random.random() < 0.03
    c = {"id": hashlib.sha1(f"S{i}".encode()).hexdigest()[:10], "ncic": "02507", "number_of_units": nunits, "unit_in_error": 1,
         "county": 25, "locality": "City", "city": "Hilliard", "severity_code": sev[0], "severity": sev[1],
         "location_road_name": spot[0].rsplit(" ", 1)[0], "location_road": spot[0], "reference_road": spot[1],
         "latitude": round(lat, 6), "longitude": round(lon, 6), "reference_point": "Intersection",
         "within_intersection": random.random() < 0.55, "within_interchange": "ramp" in spot[1], "roadway_divided": random.random() < 0.4,
         "first_harmful_event_location": "On roadway", "manner_of_collision": manner, "light_condition": light, "weather": weather,
         "road_condition": random.choice(ROADCOND[weather]), "surface": "Blacktop/Asphalt", "contour": "Straight level",
         "work_zone_related": random.random() < 0.03, "active_school_zone": random.random() < 0.03, "private_property": random.random() < 0.08,
         "hit_skip": "Unsolved" if random.random() < 0.05 else None, "narrative": "SAMPLE DATA – synthetic record generated for dashboard preview.",
         "crash_datetime": when.strftime("%Y-%m-%dT%H:%M"), "crash_date": when.strftime("%Y-%m-%d"), "year": when.year, "month": when.month,
         "hour": h, "day_of_week": DOW[when.weekday()], "response_minutes": round(random.uniform(2, 9), 1),
         "driver_ages": [p["age"] for p in drivers], "driver_age_bands": [p["age_band"] for p in drivers], "driver_genders": [p["gender"] for p in drivers],
         "unit_in_error_driver_age": err["age"], "unit_in_error_driver_age_band": err["age_band"], "unit_in_error_driver_gender": err["gender"],
         "primary_contributing_circumstance": units[0]["contributing_circumstance"], "primary_unit_type": units[0]["unit_type"],
         "alcohol_suspected": any(p["alcohol_suspected"] for p in drivers), "drug_suspected": any(p["marijuana_suspected"] for p in drivers),
         "distracted": any(p["distracted_by"] != "Not distracted" for p in drivers), "citation_issued": any(p["cited"] for p in drivers),
         "persons_count": len(persons), "injured_count": sum(1 for p in persons if p["injury_code"] <= 4),
         "fatal_count": sum(1 for p in persons if p["injury_code"] == 1), "unit_types": sorted({u["unit_type"] for u in units}),
         "pedestrian_or_bicycle": ped, "motorcycle": any(u["unit_type"].startswith("Motorcycle") for u in units),
         "units": units, "persons": persons, "source_file": "synthetic"}
    if ped:
        c["unit_types"].append(random.choice(["Pedestrian/Skater", "Bicycle"]))
    crashes.append(c)
crashes.sort(key=lambda c: c["crash_datetime"])
os.makedirs("data", exist_ok=True)
with open("data/sample.json", "w") as f:
    json.dump({"generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "count": len(crashes), "synthetic": True, "crashes": crashes}, f)
print(len(crashes), "synthetic crashes written to data/sample.json")
