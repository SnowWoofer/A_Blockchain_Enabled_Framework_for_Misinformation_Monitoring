import argparse
import json
import os
import re
from collections import defaultdict
import pandas as pd
from datasets import load_dataset
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DATA_DIR = os.environ.get("DATA_DIR", ".")
RAW_DIR = os.path.join(DATA_DIR, "data/raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "data/processed")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)


def clean_text(text: str):
    """Strips URLs, RT tags, boilerplate news phrases, and @mentions from a tweet,
    returning the cleaned text plus a comma-joined list of anything stripped out."""
    if not isinstance(text, str):
        return "", "unspecified"
    pattern = (
        r'https\S+|pic\.twitter\.com\S+|(?:\S+\s)?\(Reuters\)\s*-|Featured Image.*|'
        r'entire story:.*|https?://\S+|RT\s@\S+|Read\s+more.*$|Via\s+:\S$'
    )
    twitter_usernames = r'@\S+'
    combined = f"{pattern}|{twitter_usernames}"

    urls = re.findall(combined, text, flags=re.IGNORECASE)
    text = re.sub(pattern, '', text)
    cleaned = re.sub(r'\s+', ' ', text)
    source_str = ", ".join(urls) if urls else "unspecified"
    return cleaned.strip(), source_str


def get_dataset():
    """Downloads and caches the raw English twitter-misinformation dataset,
    cleaning text and extracting source URLs along the way."""
    eng_filename = os.path.join(RAW_DIR, "twitter_data_eng_raw.csv")
    if os.path.exists(eng_filename):
        return

    print(f"Raw dataset missing at {eng_filename}, downloading...")
    ds = load_dataset("roupenminassian/twitter-misinformation")
    df_train = ds["train"].to_pandas()
    df_train.insert(0, 'set', 'train')
    df_test = ds["test"].to_pandas()
    df_test.insert(0, 'set', 'test')

    raw_data = pd.concat([df_train, df_test], ignore_index=True)
    raw_data.insert(len(raw_data.columns), 'source', "")
    raw_data = raw_data[['set', 'text', 'label', 'source']]
    raw_data['text'], raw_data['source'] = zip(*raw_data['text'].apply(clean_text))
    raw_data.to_csv(eng_filename, index=False, encoding='utf-8')


def split_text_by_chars(text: str, max_chars: int = 4000) -> list:
    """Safely slices text into ~max_chars blocks without breaking words."""
    if not text:
        return [""]

    words = text.split(" ")
    chunks = []
    current_chunk = []
    current_length = 0

    for word in words:
        if current_length + len(word) + 1 > max_chars:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            current_chunk = [word]
            current_length = len(word)
        else:
            current_chunk.append(word)
            current_length += len(word) + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks if chunks else [""]


import json
import os
import pandas as pd


def build_bulletproof_batch_jsonl(
    src_filename: str,
    output_prefix: str,
    src_lang: str,
    dest_lang: str,
    max_file_size_mb: int = 90,
    max_requests_per_file: int = 45000,  # Strict safety line below 50k
):
    """Generates chunked JSONL files that strictly respect both the 100MB

    file size limit AND the 50,000 total request line cap.
    """
    print(f"📦 Processing dataset: {src_filename}...")
    df = pd.read_csv(src_filename, encoding="utf-8")

    system_prompt = (
        f"You are a professional native-level translator fluent in {src_lang} and {dest_lang}.\n"
        f"Translate the user text exactly from {src_lang} to {dest_lang}.\n"
        f"Return ONLY the direct translation. Do not add conversational intro text, markdown, or commentary."
    )

    part_idx = 1
    generated_files = []
    current_file_path = f"{output_prefix}_part_{part_idx}.jsonl"
    f = open(current_file_path, "w", encoding="utf-8")
    generated_files.append(current_file_path)

    request_counter = 0
    print(f"✍️ Writing to {current_file_path}...")

    for idx, row in df.iterrows():
        actual_global_idx = (
            row["global_index"] if "global_index" in df.columns else idx
        )
        text = str(row["text"]).strip() if pd.notna(row["text"]) else ""

        # Use your split_text_by_chars function here
        chunks = split_text_by_chars(text, max_chars=4000)

        for chunk_idx, chunk_text in enumerate(chunks):
            custom_id = f"row_{actual_global_idx}_chunk_{chunk_idx}"

            request_object = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4o-mini",
                    "temperature": 0.3,
                    "frequency_penalty": 0.5,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": chunk_text if chunk_text else " ",
                        },
                    ],
                },
            }

            f.write(json.dumps(request_object) + "\n")
            request_counter += 1

            # Trigger a file split if we cross either threshold
            if request_counter >= max_requests_per_file:
                f.close()
                print(
                    f"⚠️ {current_file_path} hit the {request_counter:,} request line limit. Splitting..."
                )
                part_idx += 1
                request_counter = 0
                current_file_path = f"{output_prefix}_part_{part_idx}.jsonl"
                f = open(current_file_path, "w", encoding="utf-8")
                generated_files.append(current_file_path)
                print(f"✍️ Writing to {current_file_path}...")

        # Secondary fallback size check every 1000 main data rows
        if idx % 1000 == 0:
            f.flush()
            file_size_mb = os.path.getsize(current_file_path) / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                f.close()
                print(
                    f"⚠️ {current_file_path} reached {file_size_mb:.2f} MB. Splitting..."
                )
                part_idx += 1
                request_counter = 0
                current_file_path = f"{output_prefix}_part_{part_idx}.jsonl"
                f = open(current_file_path, "w", encoding="utf-8")
                generated_files.append(current_file_path)
                print(f"✍️ Writing to {current_file_path}...")

    f.close()
    print(f"✅ Safe files constructed: {generated_files}")
    return generated_files

def submit_batch_job(jsonl_input_path: str) -> str:
    print(f"Uploading {jsonl_input_path} to OpenAI...")
    batch_file = client.files.create(file=open(jsonl_input_path, "rb"), purpose="batch")
    print(f"File uploaded successfully. File ID: {batch_file.id}")

    print("Submitting batch execution request...")
    job = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    print(f"Batch job created successfully! Job ID: {job.id}")
    return job.id


def check_job_status(job_id: str):
    job = client.batches.retrieve(job_id)
    print(f"Job ID: {job.id} | Status: {job.status}")
    if job.status == "completed":
        print(f"Output file ID to download: {job.output_file_id}")
    elif job.status == "failed":
        print(f"Job failed. Error details: {job.errors}")
    return job


def compile_results_to_dataframe(original_csv: str, results_file_ids: list, output_csv: str, new_column: str):
    """
    FIXED: Downloads and securely groups all text chunks by row index,
    sorting sequentially to rebuild multi-chunk articles flawlessly.
    """
    print(f"🧵 Reassembling data from {results_file_ids} back into {original_csv}...")
    df = pd.read_csv(original_csv, encoding="utf-8")
    
    # Initialize column if it doesn't exist
    if new_column not in df.columns:
        df[new_column] = ""

    # Temporary storage mapping structure: row_index -> { chunk_index: content }
    assembled_data = defaultdict(dict)

    for file_id in results_file_ids:
        print(f"Downloading processing components from file content token ID: {file_id}...")
        file_response = client.files.content(file_id)
        results_text = file_response.text

        for line in results_text.strip().split("\n"):
            if not line:
                continue
            data = json.loads(line)
            custom_id = data["custom_id"]
            
            # Deconstruct tracking pointers
            parts = custom_id.split("_")
            row_idx = int(parts[1])
            chunk_idx = int(parts[3])

            if data["response"]["status_code"] == 200:
                translated_content = data["response"]["body"]["choices"][0]["message"]["content"]
                assembled_data[row_idx][chunk_idx] = translated_content.strip()
            else:
                assembled_data[row_idx][chunk_idx] = "[API_PROCESSING_ERROR]"

    # Stitch pieces together in strict ascending chronological order
    print("Stitching array fragments back down onto core DataFrame indexes...")
    for row_idx, chunks_dict in assembled_data.items():
        sorted_chunks = [chunks_dict[k] for k in sorted(chunks_dict.keys())]
        full_text = " ".join(sorted_chunks)
        df.at[row_idx, new_column] = full_text

    df.to_csv(output_csv, index=False, encoding="utf-8")
    print(f"🎉 Fully stitched state saved successfully to {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest_lang", type=str, required=True)
    parser.add_argument("--src_lang", type=str, default="eng")
    parser.add_argument("--start_index", type=int, default=0)
    parser.add_argument("--end_index", type=int, default=None)
    parser.add_argument("--part_tag", type=str, default="run")
    args = parser.parse_args()

    get_dataset()

    src_file = os.path.join(RAW_DIR, "twitter_data_eng_raw.csv")
    df_full = pd.read_csv(src_file, encoding='utf-8')
    
    # Inject absolute global tracking metrics prior to slicing
    df_full['global_index'] = df_full.index

    end_idx = args.end_index if args.end_index is not None else len(df_full)
    df_slice = df_full.iloc[args.start_index:end_idx].copy()
    
    temp_slice_csv = os.path.join(RAW_DIR, f"twitter_data_eng_slice_{args.part_tag}.csv")
    df_slice.to_csv(temp_slice_csv, index=False, encoding='utf-8')

    # Build split files safely containing loop defense protections
    jsonl_prefix = f"{args.src_lang}_{args.dest_lang}_batch_{args.part_tag}"
    generated_jsonl_files = build_bulletproof_batch_jsonl(
        src_filename=temp_slice_csv,
        output_prefix=jsonl_prefix,
        src_lang=args.src_lang,
        dest_lang=args.dest_lang,
    )

    # Submit jobs sequentially for all components generated below 100MB bounds
    for jsonl_file in generated_jsonl_files:
        job_id = submit_batch_job(jsonl_file)