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

def get_dataset():
    eng_filename = os.path.join(RAW_DIR, "twitter_data_eng_raw.csv")
            
    if not os.path.exists(eng_filename):   #get the dataset agin in not existaneat or wrong size
        print(f"Raw Dataset Missing For {eng_filename}...\nGetting Original Dataset...")
        ds = load_dataset("roupenminassian/twitter-misinformation")
        df_train = ds["train"].to_pandas()
        df_train.insert(0, 'set', 'train')
        df_test = ds["test"].to_pandas()
        df_test.insert(0, 'set', 'test')
        raw_data = pd.concat([df_train, df_test], ignore_index=True)
        raw_data.insert(len(raw_data.columns), 'source', "")
        raw_data = raw_data[['set','text', 'label', 'source']]
        raw_data['text'], raw_data['source'] = zip(*raw_data['text'].apply(clean_text))
        raw_data.to_csv(eng_filename)


def translate_dataset(dest_lang,src_lang,model,tokenizer,device,back_trans:bool):
    dest_filename = os.path.join(RAW_DIR, f"twitter_data_{dest_lang}_raw.csv")
    src_filename = os.path.join(RAW_DIR, f"twitter_data_{src_lang}_raw.csv")
    back_filename = os.path.join(RAW_DIR, f"{dest_lang}_to_{src_lang}_back.csv")

    if not os.path.exists(dest_filename) or True:
        print(f"Raw Dataset Missing For {dest_lang}...\nTranslating...")
        if not os.path.exists(src_filename):
            raise FileNotFoundError(f"{src_lang} Dataset Needs To Be Generated First...")
        src_base = pd.read_csv(src_filename)
        
        if os.path.exists(dest_filename):
            already_done = len(pd.read_csv(dest_filename))
        else:
            already_done = 0
        
        batch_size = 32
        for i in range(already_done, len(src_base), batch_size):
            batch = src_base['text'].iloc[i:i+batch_size].fillna("").astype(str).tolist()
            translated_batch = translate_text(batch, model, tokenizer, device, dest_lang)
            batch_df = pd.DataFrame({'text': translated_batch})
            #translated_text.extend(translated_batch)
            batch_df.to_csv(dest_filename, mode='a', header=not os.path.exists(dest_filename), index=False)
            if back_trans == True:
                translated_batch_back = translate_text(translated_batch, model, tokenizer, device, src_lang)
                batch_df_back = pd.DataFrame({'text': translated_batch_back})
                #translated_text.extend(translated_batch)
                batch_df_back.to_csv(back_filename, mode='a', header=not os.path.exists(back_filename), index=False)
            print(f"Batch {i}[{batch_size*i} records translated] completed ")
    
    print("Raw Dataset Translated into {dest_lang}...")
    
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
    get_dataset()
    model,tokenizer,device = translate_init()
    translate_dataset("nso","eng",model,tokenizer,device,True)
    translate_dataset("zul","eng",model,tokenizer,device,True)

    # out = jiwer.process_words(test_text, test_back_translated)
    # print(jiwer.visualize_alignment(out))    
