import json
import pandas as pd
import argparse
from pathlib import Path

# Automatically finds the root directory of your project (unchanged, this part was fine)
PROJECT_ROOT = next(p for p in Path(__file__).parents if (p / ".git").exists())


def load_jsonl(filepath, original):
    full_path = PROJECT_ROOT / filepath
    original_path = PROJECT_ROOT / original  # build the original CSV's path the same way

    records = []
    with open(full_path, "r", encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records.append(record)

    gpt_df = pd.DataFrame(records)
    print(f"\ngpt_df.head():\n{gpt_df.head()}\n")
    print(f"gpt_df.columns.tolist(): {gpt_df.columns.tolist()}\n")

    # --- 2.b: rename to match NLLB schema ---
    gpt_df = gpt_df.rename(columns={
        "nso_translation_full": "translated",
        "back_translation_full": "back_translated",
    })

    # --- 2.c: load original English CSV and join on row_id ---
    eng_df = pd.read_csv(original_path, encoding='utf-8')
    eng_df = eng_df.reset_index().rename(columns={"index": "row_id"})

    merged = eng_df.merge(gpt_df, on="row_id", how="inner")
    print(f"eng rows: {len(eng_df)}, gpt rows: {len(gpt_df)}, merged rows: {len(merged)}")

    # --- 2.d: select final columns, now keeping row_id/source/label alongside translations ---
    final_df = merged[["row_id", "text", "translated", "back_translated", "source", "label"]].copy()

    return final_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--load_jsonl", action="store_true")
    parser.add_argument("--filepath", default="")
    parser.add_argument("--original_file", default="")  # fixed typo, matches usage below
    args = parser.parse_args()

    print(f"Received filepath argument: {args.filepath}")

    if args.load_jsonl and args.filepath:
        final_df = load_jsonl(filepath=args.filepath, original=args.original_file)

        # --- 2.e: spot-check ---
        print(final_df.sample(min(5, len(final_df)), random_state=42))

        # --- 2.f: save ---
        out_path = PROJECT_ROOT / "data/raw/twitter_data_nso_raw.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        final_df.to_csv(out_path, index=False)
        print(f"Saved {len(final_df)} rows to {out_path}")