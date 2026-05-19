# ID Assigner

Assigns a mock ID sequentially to every student with consecutive submissions

Note that this only works here because the filtered submissions have the submissions from the same student bunched up next
to each other. This is specific to this project since the Gradescope scraper is set up this way

## Usage

```bash
run --package id-assigner --bin id-assigner -- <input.csv> <output.csv>
```
