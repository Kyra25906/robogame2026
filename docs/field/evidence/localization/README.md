# Localization navigation acceptance evidence

## Source

- Source branch: `origin/feature/localization`
- Source commit: `2f2fd33970f80e58b096273a03d8939807b7667a`
- Original commit message: `test(localization): add 10-round navigation acceptance results`
- Recorded date: 2026-08-11

The CSV contents are preserved from the source commit. Only their directory and
file names were changed during integration.

## Files

- `navigation_10_rounds.csv`: 10 rounds, 5 targets per round, 50 records.
- `navigation_1_round.csv`: one additional 5-target run.

For `navigation_10_rounds.csv`, all 50 records are marked as passed. The
recorded mean position error is 0.0305 m, the maximum position error is 0.05 m,
the mean yaw error is 0.0514 rad, the maximum yaw error is 0.0865 rad, and the
mean duration is 2.608 s.

## Evidence boundary

The source commit does not include the runtime command, ROS bag, hardware
identity, localization input source, or raw sensor logs needed to establish a
real-robot measurement chain. The results are highly repeatable across rounds.
Until the original operator supplies that provenance, treat these files as
simulation or software navigation-acceptance evidence, not proof of real-robot
localization accuracy.

