#!/usr/bin/env python3
"""
batch_worker.py — resumable, shardable batch runner for oh1_ocr_extract over a big folder of OH-1 PDFs.

    python3 batch_worker.py scan   <folder> --work <workdir> [--shard i/n] [--time-budget SEC] [--limit N]
    python3 batch_worker.py assemble --work <workdir> -o <data folder> [--keep-report-number]

scan:      walks <folder> for PDFs, skips the ones already recorded in <workdir>/results/*.jsonl,
           parses files one at a time and appends one JSON line per file (success or failure) to
           <workdir>/results/shard_<i>.jsonl, stopping when the time budget is used up. Safe to re-run
           any number of times; several shards can run concurrently.
assemble:  merges every jsonl into crashes.json / crashes.csv / persons.csv (same format as oh1_extract.py).
"""
import argparse, glob, hashlib, json, os, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oh1_extract as T
import oh1_ocr_extract as X

def all_pdfs(folder):
    out = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith("_") and not d.startswith(".")]
        out += [os.path.join(root, n) for n in names if n.lower().endswith(".pdf") and not n.startswith(".")]
    return sorted(out)

def done_set(work, retry_failures=False):
    done, failed = set(), set()
    for f in glob.glob(os.path.join(work, "results", "*.jsonl")):
        with open(f) as fh:
            for line in fh:
                try: r = json.loads(line)
                except Exception: continue
                done.add(r["file"])
                if "error" in r: failed.add(r["file"])
                else: failed.discard(r["file"])
    return (done - failed) if retry_failures else done

def scan(args):
    os.makedirs(os.path.join(args.work, "results"), exist_ok=True)
    i, n = (map(int, args.shard.split("/")) if args.shard else (0, 1))
    files = [f for k, f in enumerate(all_pdfs(args.folder)) if k % n == i]
    done = done_set(args.work, args.retry_failures)
    todo = [f for f in files if os.path.relpath(f, args.folder) not in done]
    out = os.path.join(args.work, "results", f"shard_{i}.jsonl")
    t0 = time.time(); k = 0
    print(f"shard {i}/{n}: {len(files)} files, {len(todo)} to do", flush=True)
    for path in todo:
        if args.time_budget and time.time() - t0 > args.time_budget: break
        if args.limit and k >= args.limit: break
        rel = os.path.relpath(path, args.folder); t1 = time.time()
        rec = {"file": rel}
        try:
            c = X.process(path, True)            # keep the report number here; hashing happens at assemble time
            rec["crash"] = c
            print(f"  ok   {rel:45s} {c.get('crash_date')} {str(c.get('hour')):>4}h {c.get('severity_code')} {c.get('location_road')} / {c.get('reference_road')} [{c.get('source_type')}, {time.time()-t1:.0f}s]", flush=True)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            print(f"  FAIL {rel}: {rec['error']}", flush=True)
        with open(out, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        k += 1
    print(f"shard {i}/{n}: {k} files this run, {len(todo) - k} left, {time.time()-t0:.0f}s", flush=True)

def assemble(args):
    recs = {}
    for f in sorted(glob.glob(os.path.join(args.work, "results", "*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try: r = json.loads(line)
                except Exception: continue
                recs[r["file"]] = r            # last record for a file wins
    crashes, failures = {}, {}
    for rel, r in recs.items():
        if "crash" not in r:
            failures[rel] = r.get("error", "?"); continue
        c = r["crash"]
        rn = c.get("report_number") or c.get("id") or rel
        c["id"] = rn if args.keep_report_number else hashlib.sha1(str(rn).encode()).hexdigest()[:10]
        if not args.keep_report_number: c.pop("report_number", None)
        c["source_file"] = os.path.basename(rel)
        crashes[c["id"]] = c
    out = sorted(crashes.values(), key=lambda c: c.get("crash_datetime") or "")
    os.makedirs(args.out, exist_ok=True)
    T.write_outputs(out, args.out)
    jp = os.path.join(args.out, "crashes.json")
    with open(jp) as fh: blob = json.load(fh)
    blob["failures"] = failures
    blob["source_counts"] = {"text": sum(1 for c in out if c.get("source_type") == "text"), "scan": sum(1 for c in out if c.get("source_type") == "scan")}
    with open(jp, "w") as fh: json.dump(blob, fh, indent=1)
    print(f"{len(out)} crashes, {len(failures)} failures -> {args.out}/")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("scan"); a.add_argument("folder"); a.add_argument("--work", required=True); a.add_argument("--shard", default="")
    a.add_argument("--time-budget", type=int, default=0); a.add_argument("--limit", type=int, default=0); a.add_argument("--retry-failures", action="store_true")
    b = sub.add_parser("assemble"); b.add_argument("--work", required=True); b.add_argument("-o", "--out", default="data"); b.add_argument("--keep-report-number", action="store_true")
    args = ap.parse_args()
    scan(args) if args.cmd == "scan" else assemble(args)

if __name__ == "__main__":
    main()
