# Integration and acceptance checklist

## Week 1 gate

- [ ] Hardware emergency stop independently verified.
- [ ] Serial connection runs for 30 minutes without a parser or heartbeat failure.
- [ ] MCU stops within 150 ms after command loss.
- [ ] Positive `vx`, `vy`, and `wz` match the agreed coordinate system.
- [ ] Grab, release, lift, and stop commands return explicit results.

## Week 2 gate

- [ ] 8 of 10 navigation trials finish within 5 cm and 5 degrees.
- [ ] Orange and purple recall each reach 90% on recorded validation data.
- [ ] Visual alignment succeeds in 18 of 20 randomized placements.
- [ ] Each color is grabbed successfully in at least 8 of 10 trials.

## Week 3 decision gate

- [ ] Grab succeeds 27/30.
- [ ] Single placement succeeds 18/20 and remains stable after 3 seconds.
- [ ] Full single-cube loop succeeds at least 8/10.
- [ ] Communication loss, stale vision, and mechanism failure stop safely.

If any item fails, freeze the roof-tower branch and make the single-cube loop reach 9/10.

## Week 4 roof-tower gate

- [ ] Three-cube carriage succeeds 8/10 without drops.
- [ ] Two orange layers remain stable 8/10.
- [ ] Purple roof placement remains stable 7/10.
- [ ] Full roof tower succeeds 6/10.
- [ ] Cargo counts never advance after a failed grab or drop.

