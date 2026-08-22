import { expect, test } from "bun:test"

import { findSkillConflicts } from "../lib/run-eval"

test("findSkillConflicts returns locations for matching skills", () => {
  expect(
    findSkillConflicts(
      JSON.stringify([
        { name: "other-skill", location: "/tmp/other" },
        { name: "target-skill", location: "/tmp/target" },
      ]),
      "target-skill",
    ),
  ).toEqual(["/tmp/target"])
})

test("findSkillConflicts uses unknown location when matching entry has no location", () => {
  expect(
    findSkillConflicts(JSON.stringify([{ name: "target-skill" }]), "target-skill"),
  ).toEqual(["unknown location"])
})

test("findSkillConflicts returns empty array when there is no match", () => {
  expect(
    findSkillConflicts(
      JSON.stringify([{ name: "other-skill", location: "/tmp/other" }]),
      "target-skill",
    ),
  ).toEqual([])
})

test("findSkillConflicts returns empty array for invalid JSON", () => {
  expect(findSkillConflicts("{", "target-skill")).toEqual([])
})

test("findSkillConflicts returns empty array for non-array JSON", () => {
  expect(findSkillConflicts(JSON.stringify({ name: "target-skill" }), "target-skill")).toEqual([])
})
