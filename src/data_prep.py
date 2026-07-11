import pandas as pd
from datasets import load_dataset
import os
import jiwer
import re
import argparse
import json
import functools
import httpx
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
from dotenv import load_dotenv
import inspect

# Force stdout to flush immediately so print() shows up live,
# even when not running with `python -u`
print = functools.partial(print, flush=True)

load_dotenv()

# Explicit per-phase timeouts. A single `timeout=180` still lets the TLS
# handshake phase hang indefinitely on some Windows setups (antivirus SSL
# inspection, corporate proxy/VPN, flaky adapter). This forces the connect/
# handshake phase specifically to give up after 15s instead of hanging
# forever, while still giving generation up to 180s to finish.
_timeout = httpx.Timeout(connect=15.0, read=180.0, write=30.0, pool=15.0)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=_timeout, max_retries=0)

DATA_DIR = os.environ.get("DATA_DIR", ".")
RAW_DIR = os.path.join(DATA_DIR, "data/raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "data/processed")

if not os.path.exists(RAW_DIR):
    os.makedirs(RAW_DIR)
    print(f"{RAW_DIR} created...")

if not os.path.exists(PROCESSED_DIR):
    os.makedirs(PROCESSED_DIR)
    print(f"{PROCESSED_DIR} created...")


def get_dataset():
    print(f"Line: {inspect.currentframe().f_lineno}")
    eng_filename = os.path.join(RAW_DIR, "twitter_data_eng_raw.csv")
    if not os.path.exists(eng_filename):
        print(f"Raw Dataset Missing For {eng_filename}...\nGetting Original Dataset...")
        ds = load_dataset("roupenminassian/twitter-misinformation")
        df_train = ds["train"].to_pandas()
        df_train.insert(0, 'set', 'train')
        df_test = ds["test"].to_pandas()
        df_test.insert(0, 'set', 'test')
        raw_data = pd.concat([df_train, df_test], ignore_index=True)
        raw_data.insert(len(raw_data.columns), 'source', "")
        raw_data = raw_data[['set', 'text', 'label', 'source']]
        raw_data['text'], raw_data['source'] = zip(*raw_data['text'].apply(clean_text))
        raw_data.to_csv(eng_filename)
    print("eng_dataset loaded")
    print(f"Line: {inspect.currentframe().f_lineno}")

def clean_text(text):
    print(f"Line: {inspect.currentframe().f_lineno}")
    urls = re.findall(r'https\S+|pic\.twitter\.com\S+', text)
    cleaned = re.sub(r'http\S+|pic\.twitter\.com\S+', '', text)
    source_str = ", ".join(urls) if urls else ""
    print(f"Line: {inspect.currentframe().f_lineno}")
    return cleaned.strip(), source_str


def build_system_prompt(src_lang: str, dest_lang: str) -> str:
    print(f"Line: {inspect.currentframe().f_lineno}")
    return (
        f"You are a professional native-level translator fluent in both "
        f"{src_lang} and {dest_lang}.\n\n"
        f"TASK:\n"
        f"For each input sentence: (1) translate it from {src_lang} to {dest_lang}, "
        f"then (2) independently back-translate that translation to {src_lang}, "
        f"as if you had never seen the original.\n\n"
        f"OUTPUT FORMAT (strict):\n"
        f"Return ONLY a single valid JSON array. No preamble, no explanation, "
        f"no markdown code fences, no trailing commentary.\n"
        f"Each array element must be an object with exactly these keys:\n"
        f'  "id": integer, sequential starting at 1, matching input order\n'
        f'  "original_text": string, copied exactly from the input\n'
        f'  "translated_text": string\n'
        f'  "back_translated_text": string\n\n'
        f"RULES:\n"
        f"- Preserve the exact number of input sentences in the output — "
        f"never merge, split, or skip any, even if a sentence is empty, very short, "
        f"or contains only a URL/emoji/hashtag.\n"
        f'- If an input sentence is empty, return "" for translated_text and '
        f"back_translated_text, not null.\n"
        f"- Do not add explanations, notes, or apologies inside any field.\n"
        f"- Ensure the JSON is syntactically valid: escape quotes/newlines properly."
    )


def build_user_prompt(batch: list) -> str:
    print(f"Line: {inspect.currentframe().f_lineno}")
    numbered = "\n".join(f"{i + 1}. {text}" for i, text in enumerate(batch))
    return (
        f"Translate the following {len(batch)} numbered sentences. "
        f"Each output id must correspond to its input number.\n\n"
        f"{numbered}"
    )


def translate_batch_gpt(batch: list, src_lang: str, dest_lang: str, max_retries: int = 2):
    print(f"Line: {inspect.currentframe().f_lineno}")
    system_prompt = build_system_prompt(src_lang, dest_lang)
    user_prompt = build_user_prompt(batch)
    expected_ids = list(range(1, len(batch) + 1))

    # Rough token budget: each item needs original + translated + back_translated text,
    # roughly 4x the original length in tokens (safe multiplier), plus JSON overhead
    # (quotes, keys, brackets) per item (~20 tokens), plus a fixed buffer.
    approx_input_chars = sum(len(t) for t in batch)
    max_out_tokens = min(16000, max(1024, int(approx_input_chars * 4) + len(batch) * 40 + 500))

    print(f"Line: {inspect.currentframe().f_lineno}")
    for attempt in range(max_retries + 1):
        try:
            print(f"Line: {inspect.currentframe().f_lineno}: sending request, attempt {attempt}, max_out_tokens={max_out_tokens}")
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
                max_tokens=max_out_tokens,
            )
            print(f"Line: {inspect.currentframe().f_lineno}: response received")
            finish_reason = response.choices[0].finish_reason
            raw = response.choices[0].message.content.strip()
            print(f"Line: {inspect.currentframe().f_lineno}: finish_reason={finish_reason}, raw_len={len(raw)}")
            if finish_reason == "length":
                # The model got cut off before finishing the JSON — this is exactly
                # what produced your "Unterminated string" error. Don't even try to
                # parse it; retry immediately with a bigger budget.
                max_out_tokens = min(16000, int(max_out_tokens * 1.5))
                print(f"Attempt {attempt}: response truncated (finish_reason=length), "
                      f"retrying with max_tokens={max_out_tokens}")
                continue

            parsed = json.loads(raw)
            actual_ids = [item["id"] for item in parsed]
            if actual_ids == expected_ids:
                return parsed
            print(f"Attempt {attempt}: id mismatch — got {len(actual_ids)}, expected {len(expected_ids)}")

        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as e:
            print(f"Attempt {attempt}: API error — {type(e).__name__}: {e}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Attempt {attempt}: parse failed — {type(e).__name__}: {e}")
        except Exception as e:
            # Catch-all safety net: anything not covered above (e.g. raw ssl.SSLError,
            # socket-level errors that slip past httpx's own error wrapping) gets
            # logged and retried instead of silently hanging past our httpx timeout.
            print(f"Attempt {attempt}: UNEXPECTED error — {type(e).__name__}: {e}")

    raise RuntimeError(f"Failed to get valid batch translation after {max_retries + 1} attempts")


def translate_dataset_gpt(dest_lang, src_lang, start_index: int = 0, end_index: int = None, part_tag: str = ""):
    print(f"Line: {inspect.currentframe().f_lineno}")
    dest_filename = os.path.join(RAW_DIR, f"twitter_data_{dest_lang}_gpt{part_tag}.csv")
    src_filename = os.path.join(RAW_DIR, f"twitter_data_{src_lang}_raw.csv")

    if not os.path.exists(src_filename):
        raise FileNotFoundError(f"{src_lang} Dataset Needs To Be Generated First...")

    src_base = pd.read_csv(src_filename, encoding='utf-8')

    if end_index is None:
        end_index = len(src_base)
    print(f"Line: {inspect.currentframe().f_lineno}")
    already_done = len(pd.read_csv(dest_filename, encoding='utf-8')) if os.path.exists(dest_filename) else 0
    resume_from = start_index + already_done

    # Smaller batch = smaller JSON response = less truncation risk, and cheaper
    # retries when something does go wrong.
    batch_size = 8
    print(f"Line: {inspect.currentframe().f_lineno}")
    for i in range(resume_from, end_index, batch_size):
        batch_end = min(i + batch_size, end_index)
        batch = src_base['text'].iloc[i:batch_end].fillna("").astype(str).tolist()

        try:
            results = translate_batch_gpt(batch, src_lang, dest_lang)
        except RuntimeError as e:
            print(f"Rows {i}-{batch_end} FAILED, stopping so checkpoint stays accurate: {e}")
            break  # don't write partial/bad data — resume_from will retry this batch next run

        batch_df = pd.DataFrame(results)
        batch_df.to_csv(dest_filename, mode='a', header=not os.path.exists(dest_filename), index=False, encoding='utf-8')
        print(f"Rows {i} to {batch_end} completed")
    print(f"Line: {inspect.currentframe().f_lineno}")
    print(f"GPT translation into {dest_lang} done (or paused on error).")


if __name__ == "__main__":
    print(f"Line: {inspect.currentframe().f_lineno}")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest_lang", type=str, required=True)
    parser.add_argument("--src_lang", type=str, required=True, default="eng")
    parser.add_argument("--start_index", type=int, default=0)
    parser.add_argument("--end_index", type=int, default=None)
    parser.add_argument("--part_tag", type=str, default="")
    args = parser.parse_args()

    print(f"Line: {inspect.currentframe().f_lineno}")

    get_dataset()

    translate_dataset_gpt(
        dest_lang=args.dest_lang,
        src_lang=args.src_lang,
        start_index=args.start_index,
        end_index=args.end_index,
        part_tag=args.part_tag,
    )