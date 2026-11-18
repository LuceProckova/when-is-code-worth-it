---
name: label-workflow-steps
description: Decide for every step of a workflow whether a person, code or a model should do it, with the criterion behind each label. Use when a new workflow is built or when the runs suggest a step is in the wrong place.
---

# Label workflow steps

You are the designer of a personal assistant. You get the steps of one workflow. For every step, decide who does it and name the criterion that decided it.

## Labels

- `person`: the person does the step or must approve it.
- `code`: plain code does the step with a rule, a lookup or a check.
- `code_first_model_fallback`: code tries first and a model only handles what code cannot read.
- `model`: a language model does the step.
- `runner`: not a step at all. The plain program around the steps does it, because it decides nothing.

## Checks, always in this order

Go through the checks from top to bottom. The first one that fits decides the label. Do not skip ahead.

1. **Runner.** The work decides nothing a person needs to see, such as calling the steps in order, saving, marking ready or calling a connector with values earlier steps already settled. Label `runner`.
2. **Person.** Label `person` if the step:
   - P1 is a matter of preference, so there is no right answer for code or a model to find (for example choosing between two options the person cares about),
   - P2 cannot be undone, so no later check could catch a mistake (for example sending something to another person),
   - P3 is the last step of an official process, where people have the right not to be subject to a decision made only by automated processing,
   - P4 needs the person because the request is unclear or contradicts itself and any guess could be wrong.
3. **Code.** Label `code` if the step:
   - C1 has one right answer by a fixed rule or lookup (for example a date from day words and today's date),
   - C2 must be exact, so a near miss is still wrong (for example an email address, an ISO date or a link),
   - C3 must be traceable to the table row or rule a value came from,
   - C4 is the last check before something is saved or sent,
   - C5 works with data that can be stored in a precise format (for example durations, addresses, contacts, naming rules),
   - C6 checks what a model step produced against the source.
4. **Split.** Label `code_first_model_fallback` if the step usually comes in fixed forms that code can read, but sometimes does not (S1), so code reads the usual forms and a model handles the rest.
5. **Model.** Label `model` if the step:
   - M1 reads free language with open phrasing,
   - M2 would need a new rule for every new phrasing, so the rule list never ends,
   - M3 depends on what the content is about, where words and meaning can point different ways,
   - M4 writes free text with no single right wording.

## Answer

For every step give its id, the label, the code of the criterion that decided it (for example `C1`, `P2`, `S1`, `M4`, or `R` for runner) and one short sentence on why. Judge each step by what it needs, not by how it happens to be built today.
