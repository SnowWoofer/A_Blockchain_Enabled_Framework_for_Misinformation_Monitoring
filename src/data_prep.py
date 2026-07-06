import pandas as pd 
from datasets import load_dataset
import os
import torch 
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import jiwer
import re

RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
if not os.path.exists(RAW_DIR): 
    os.makedirs(RAW_DIR)
    print(f"{RAW_DIR} created...")
    
if not os.path.exists(PROCESSED_DIR):
    os.makedirs(PROCESSED_DIR)
    print(f"{PROCESSED_DIR} created...")


def get_raw_dataset(lang,model,tokenizer,device):
    raw_filename = os.path.join(RAW_DIR, f"twitter_data_{lang}_raw.csv")
    eng_filename = os.path.join(RAW_DIR, "twitter_data_eng_raw.csv")
            
    if not os.path.exists(raw_filename) and lang == "eng":   #get the dataset agin in not existaneat or wrong size
        print(f"Raw Dataset Missing For {lang}...\nGetting Original Dataset...")
        ds = load_dataset("roupenminassian/twitter-misinformation")
        df_train = ds["train"].to_pandas()
        df_train.insert(0, 'set', 'train')
        df_test = ds["test"].to_pandas()
        df_test.insert(0, 'set', 'test')
        raw_data = pd.concat([df_train, df_test], ignore_index=True)
        raw_data.insert(len(raw_data.columns), 'source', "")
        raw_data = raw_data[['set','text', 'label', 'source']]
        raw_data['text'], raw_data['source'] = zip(*raw_data['text'].apply(clean_text))
        raw_data.to_csv(raw_filename)

    elif not os.path.exists(raw_filename):
        print(f"Raw Dataset Missing For {lang}...\nTranslating...")
        if not os.path.exists(eng_filename):
            raise FileNotFoundError("English Dataset Needs To Be Generated First...")
        eng_base = pd.read_csv(eng_filename)
        batch_size = 32
        translated_text = []
        for i in range(0, len(eng_base), batch_size):
            batch = eng_base['text'].iloc[i:i+batch_size].tolist()
            translated_batch = translate_text(batch, model, tokenizer, device, lang)
            translated_text.extend(translated_batch)
    
    print("Raw Dataset Loaded")
    
def translate_init():
    print("Starting Translator...")
    model_name = "facebook/nllb-200-distilled-600m"
    
    #load weights and tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

    #push model to cloud gpu
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device) # sends model/model's weights stored in ram to device allocated

    print(f"Model loaded using device {device}")
    return model, tokenizer, device 

def translate_text(text:str, model, tokenizer, device:str, lang_code:str):# -> dict:
    print("Starting Translation Of Text...")
    # Use Pythorhces 's memeory optimissation conetxt mananbger
    with torch.no_grad():
        # Cleans basic social media artifictas or truncates the text to fit the modesl context
        # Meta's nnlb ses speciifif languauage codes: "eng_Latn" for english and "nso_Latn" for sepedi

        inputs=tokenizer(
            text,
            return_tensors="pt", # ensures that teh return tensor(matrices of 1 ad 0's indicctaing text and trasnlations) is pytroch compatiable
            padding=True, # padding used since all papckets/oken need to be a fixed length thus such padding/packing/filling fo smlalller tokens is needed
            truncation=True, # truncates the lagesr texts to fit the aloocated max_length 
            max_length= 512 # max number of tokens allowed
        ).to(device)

    #generate the trnaslation tokens
    translated_tokens = model.generate(
        **inputs, # unpckas python dictionary equaiaavlent to translate_tokens(inputs_var1=..., input_var2=...)
        forced_bos_token_id=tokenizer.convert_tokens_to_ids(lang_code+"_Latn"), # forces teh brigging of the token to prefix teh lang code for sepedi
        max_length=512, # max length of genreated text
    )

    # Decode the tokens back into text
    result = tokenizer.batch_decode(translated_tokens, skip_special_tokens=True) # batch decodes tensors(1 and 0 matrcies) and skipps special tokens like prefix lang tokens and stuff like thta 
    print("Translation Completed...")
    return result

def clean_text(text):
    urls = re.findall(r'https\S+|pic\.twitter\.com\S+', text)
    cleaned = re.sub(r'http\S+|pic\.twitter\.com\S+', '', text)
    source_str = ", ".join(urls) if urls else ""
    return cleaned.strip(),source_str

if __name__ == "__main__":
    model,tokenizer,device = translate_init()
    get_raw_dataset("eng",model,tokenizer,device)
    get_raw_dataset("nso",model,tokenizer,device)
    get_raw_dataset("zul",model,tokenizer,device)

    # out = jiwer.process_words(test_text, test_back_translated)
    # print(jiwer.visualize_alignment(out))    
