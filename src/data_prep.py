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
CLEAN_PATTERN = (
    r'https\S+|pic\.twitter\.com\S+|(?:\S+\s)?\(Reuters\)\s*-|Featured Image.*|'
    r'entire story:.*|https?://\S+|RT\s@\S+|Read\s+more.*$|Via\s+:\S$|@\S+'
)

def clean_text(text: str):
    if not isinstance(text, str):
        return "", "unspecified"
    found = re.findall(CLEAN_PATTERN, text, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', re.sub(CLEAN_PATTERN, '', text)).strip()
    source_str = ", ".join(found) if found else "unspecified"
    return cleaned, source_str

def get_dataset(file):
    print(f"file: {file}")
    base, ext = os.path.splitext(file)
    print(f"base:{base}")
    print(f"ext: {ext}")
    new_file = base + ".csv"
    print(f"new file: {new_file}")
    filepath = os.path.join(RAW_DIR, new_file)
    print(f"filepath: {filepath}")
    
    if os.path.exists(filepath):
        return filepath
        
    if new_file == "twitter_data_eng_raw.csv":
        ds = load_dataset("roupenminassian/twitter-misinformation")
        df_train = ds["train"].to_pandas()
        df_train.insert(0, 'set', 'train')
        df_test = ds["test"].to_pandas()
        df_test.insert(0, 'set', 'test')
        raw_data = pd.concat([df_train, df_test], ignore_index=True)
        raw_data['source'] = ""
        raw_data = raw_data[['set', 'text', 'label', 'source']]
        raw_data['text'], raw_data['source'] = zip(*raw_data['text'].apply(clean_text))
        raw_data.to_csv(filepath, index=False, encoding='utf-8')
        return filepath
        
    elif ext == ".jsonl":
        # 1. Read the raw lines into a dataframe
        df_jsonl = pd.read_json(os.path.join(RAW_DIR, file), lines=True, encoding='utf-8')
        
        # 2. Safely extract the deep nested 'content' string from each row dictionary
        def extract_content(row_response):
            try:
                # Navigates: response -> body -> choices -> first item [0] -> message -> content
                return row_response['body']['choices'][0]['message']['content']
            except (TypeError, KeyError, IndexError):
                # Returns empty string if the row is missing information or an API error occurred
                return ""

        # 3. Create the text column using the extraction helper function
        temp = pd.DataFrame()
        temp['text'] = df_jsonl['response'].apply(extract_content)
        
        # 4. Save to CSV
        temp.to_csv(filepath, index=False, encoding='utf-8')        
        return filepath
    else:
        raise FileNotFoundError

def split_text_by_chars(text: str, max_chars: int = 4000) -> list:
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

def build_batch_jsonl(src_filename: str,output_prefix: str,src_lang: str,dest_lang: str,max_file_size_mb: int = 90,max_requests_per_file: int = 45000,):
    df = pd.read_csv(src_filename, encoding="utf-8")
    system_prompt = (
        f"You are a professional native-level translator fluent in {src_lang} and {dest_lang}.\n"
        f"Translate the user text exactly from {src_lang} to {dest_lang}.\n"
        f"Return ONLY the direct translation. Do not add conversational intro text, markdown, or commentary."
    )
    part_idx = 1
    generated_files = []

    def open_new_part():
        path = os.path.join(RAW_DIR, f"{output_prefix}_part_{part_idx}.jsonl")
        generated_files.append(path)
        return path, open(path, "w", encoding="utf-8")
    current_file_path, f = open_new_part()
    request_counter = 0
    for idx, row in df.iterrows():
        global_idx = row["global_index"] if "global_index" in df.columns else idx
        text = str(row["text"]).strip() if pd.notna(row["text"]) else ""
        for chunk_idx, chunk_text in enumerate(split_text_by_chars(text, max_chars=4000)):
            request_object = {
                "custom_id": f"row_{global_idx}_chunk_{chunk_idx}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4o-mini",
                    "temperature": 0.3,
                    "frequency_penalty": 0.5,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": chunk_text if chunk_text else " "},
                    ],
                },
            }
            f.write(json.dumps(request_object) + "\n")
            request_counter += 1
            if request_counter >= max_requests_per_file:
                f.close()
                part_idx += 1
                request_counter = 0
                current_file_path, f = open_new_part()
        if idx % 1000 == 0:
            f.flush()
            if os.path.getsize(current_file_path) / (1024 * 1024) > max_file_size_mb:
                f.close()
                part_idx += 1
                request_counter = 0
                current_file_path, f = open_new_part()
    f.close()
    print(f"Generated {len(generated_files)} batch file(s): {generated_files}")
    return generated_files

def submit_batch_job(jsonl_input_path: str) -> str:
    batch_file = client.files.create(file=open(jsonl_input_path, "rb"), purpose="batch")
    job = client.batches.create(
        input_file_id=batch_file.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    print(f"Batch job submitted. Job ID: {job.id}")
    return job.id

def check_job_status(job_id: str):
    job = client.batches.retrieve(job_id)
    print(f"Job ID: {job.id} | Status: {job.status}")
    if job.status == "completed":
        print(f"Output file ID: {job.output_file_id}")
    elif job.status == "failed":
        print(f"Job failed: {job.errors}")
    return job

def compile_results_to_dataframe(original_csv: str, results_file_ids: list, output_csv: str, new_column: str):
    """Download batch results and stitch chunked translations back into the source rows."""
    df = pd.read_csv(original_csv, encoding="utf-8")
    if new_column not in df.columns:
        df[new_column] = ""
    assembled_data = defaultdict(dict)
    for file_id in results_file_ids:
        results_text = client.files.content(file_id).text
        for line in results_text.strip().split("\n"):
            if not line:
                continue
            data = json.loads(line)
            _, row_idx, _, chunk_idx = data["custom_id"].split("_")
            row_idx, chunk_idx = int(row_idx), int(chunk_idx)
            if data["response"]["status_code"] == 200:
                content = data["response"]["body"]["choices"][0]["message"]["content"]
                assembled_data[row_idx][chunk_idx] = content.strip()
            else:
                assembled_data[row_idx][chunk_idx] = "[API_PROCESSING_ERROR]"
    for row_idx, chunks_dict in assembled_data.items():
        df.at[row_idx, new_column] = " ".join(chunks_dict[k] for k in sorted(chunks_dict))
    df.to_csv(output_csv, index=False, encoding="utf-8")
    print(f"Saved stitched output to {output_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest_lang", type=str, required=True)
    parser.add_argument("--src_lang", type=str, default="eng")
    parser.add_argument("--start_index", type=int, default=0)
    parser.add_argument("--end_index", type=int, default=None)
    parser.add_argument("--part_tag", type=str, default="run")
    parser.add_argument("--input_file", type=str, required=True)
    args = parser.parse_args()
    src_file = get_dataset(args.input_file)
    #src_file = os.path.join(RAW_DIR, args.input_file)
    df_full = pd.read_csv(src_file, encoding='utf-8')
    df_full['global_index'] = df_full.index
    end_idx = args.end_index if args.end_index is not None else len(df_full)
    df_slice = df_full.iloc[args.start_index:end_idx].copy()
    temp_slice_csv = os.path.join(RAW_DIR, f"twitter_data_eng_slice_{args.part_tag}.csv")
    df_slice.to_csv(temp_slice_csv, index=False, encoding='utf-8')
    jsonl_prefix = f"{args.src_lang}_{args.dest_lang}_batch_{args.part_tag}"
    generated_jsonl_files = build_batch_jsonl(
        src_filename=temp_slice_csv,
        output_prefix=jsonl_prefix,
        src_lang=args.src_lang,
        dest_lang=args.dest_lang,
    )
    for jsonl_file in generated_jsonl_files:
        submit_batch_job(jsonl_file)

    #get_dataset("gpt_40_mini_nso_eng_output.jsonl")