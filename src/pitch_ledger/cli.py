"""``pitch-ledger`` command line: generate, validate and check realism of matches."""

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pitch_ledger.matchlab.generator import DEFAULT_KICKOFF, MatchSpec, simulate_match
from pitch_ledger.matchlab.io import read_match, write_match
from pitch_ledger.matchlab.tracking import generate_tracking
from pitch_ledger.schema import Event, TrackingSample
from pitch_ledger.schema.enums import ScenarioId
from pitch_ledger.validation import RULES, measure, validate_match
from pitch_ledger.validation.realism import RealismCheck

PHASE_1_SCENARIOS = {ScenarioId.NONE.value}


def _match_id_for(out: Path, given: str | None) -> str:
    if given:
        return given
    return out.name if re.fullmatch(r"M\d{4}", out.name) else "M0001"


def cmd_generate(args: argparse.Namespace) -> int:
    if args.scenario not in PHASE_1_SCENARIOS:
        known = ", ".join(s.value for s in ScenarioId)
        if args.scenario in known.split(", "):
            print(f"scenario {args.scenario!r} is planned for Phase 2; use 'none'", file=sys.stderr)
        else:
            print(f"unknown scenario {args.scenario!r}; known: {known}", file=sys.stderr)
        return 2
    out = Path(args.out)
    spec = MatchSpec(
        match_id=_match_id_for(out, args.match_id),
        home_team_id=args.home,
        away_team_id=args.away,
        seed=args.seed,
        kickoff_utc=datetime.fromisoformat(args.kickoff) if args.kickoff else DEFAULT_KICKOFF,
        scenario_id=args.scenario,
    )
    sim = simulate_match(spec)
    tracking = generate_tracking(sim)
    paths = write_match(out, sim.match, sim.teams, sim.players, sim.events, tracking)
    last = sim.events[-1]
    print(
        f"{spec.match_id}: {sim.match.home_team_id} {last.score_home}-{last.score_away} "
        f"{sim.match.away_team_id}, {len(sim.events)} events, {len(tracking)} tracking rows"
    )
    for path in paths:
        print(f"  wrote {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir), with_tracking=not args.no_tracking)
    report = validate_match(
        files.match,
        files.players,
        files.events,
        None if args.no_tracking else files.tracking,
        realism=not args.no_realism,
    )
    print(
        f"{files.match.match_id}: {report.events_checked} events, "
        f"{report.tracking_checked} tracking rows checked"
    )
    counts = report.counts()
    for rule_id, rule in RULES.items():
        if rule.level == "insight":
            continue
        n = counts.get(rule_id, 0)
        status = "ok" if n == 0 else f"{n} {rule.action}"
        print(f"  {rule_id:<9} {status:<12} {rule.description}")
    for result in report.results[: args.show]:
        print(f"  ! {result.rule_id} {result.action} {result.event_id or '-'}: {result.message}")
    if len(report.results) > args.show:
        print(f"  ... {len(report.results) - args.show} more")
    print("PASS" if report.ok else "FAIL (rejected rows)")
    return 0 if report.ok else 1


def format_realism(checks: Sequence[RealismCheck]) -> str:
    """A plain-text realism table: metric, measured value, band, target, status."""
    header = f"{'metric':<22} {'value':<42} {'band':<12} {'target':<16} status"
    lines = [header, "-" * len(header)]
    for c in checks:
        band = f"{c.band.low:g}-{c.band.high:g}"
        lines.append(
            f"{c.band.metric:<22} {c.summary():<42} {band:<12} {c.band.target:<16} "
            f"{'in band' if c.ok else 'OUT'}"
        )
    return "\n".join(lines)


def cmd_realism(args: argparse.Namespace) -> int:
    files = read_match(Path(args.match_dir))
    events = [Event.model_validate(e) for e in files.events]
    tracking = [TrackingSample.model_validate(s) for s in files.tracking]
    checks = measure(files.match, files.players, events, tracking)
    print(f"{files.match.match_id} realism (scenario {files.match.scenario_id}, "
          f"seed {files.match.random_seed})")  # fmt: skip
    print(format_realism(checks))
    out = [c.band.metric for c in checks if not c.ok]
    print("all metrics in band" if not out else f"out of band: {', '.join(out)}")
    return 1 if out and args.strict else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pitch-ledger", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="simulate a synthetic match and write its files")
    gen.add_argument("--scenario", default="none", help="planted scenario (Phase 1: none)")
    gen.add_argument("--seed", type=int, default=4211, help="random seed (default 4211)")
    gen.add_argument("--out", required=True, help="output directory, e.g. data/M0001")
    gen.add_argument("--match-id", help="match ID (default: output folder name or M0001)")
    gen.add_argument("--home", default="T01", help="home team ID (default T01)")
    gen.add_argument("--away", default="T02", help="away team ID (default T02)")
    gen.add_argument("--kickoff", help="kick-off time, ISO 8601 with time zone")
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate", help="run every validation rule over a match directory")
    val.add_argument("match_dir")
    val.add_argument("--no-tracking", action="store_true", help="skip tracking rows")
    val.add_argument("--no-realism", action="store_true", help="skip realism bands (MATCH-02)")
    val.add_argument("--show", type=int, default=20, help="how many results to print")
    val.set_defaults(func=cmd_validate)

    real = sub.add_parser("realism", help="compare a match with the realism bands")
    real.add_argument("match_dir")
    real.add_argument("--strict", action="store_true", help="exit 1 if any metric is out")
    real.set_defaults(func=cmd_realism)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
