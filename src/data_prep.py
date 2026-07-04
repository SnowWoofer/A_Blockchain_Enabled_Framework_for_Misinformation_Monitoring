import pandas as pd 
from datasets import load_dataset
import os
import torch 
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
import jiwer

def get_dataset():
    
    output_dir = "data/raw"
    output_file = os.path.join(output_dir, "twitter_data_english.csv")

    if not os.path.exists(output_dir): # if the dir dosenst exist, then make it, also implies no output file exists
        os.makedirs(output_dir)
        print(f"{output_dir} created...")
        
    if not os.path.exists(output_file) or os.path.getsize(output_file)!=121581666:   #get the dataset agin in not existaneat or wrong size
        print("Dataset Missing...")
        ds = load_dataset("roupenminassian/twitter-misinformation")

        df_train = ds["train"].to_pandas()
        df_train.insert(0, 'set', 'train')
        df_test = ds["test"].to_pandas()
        df_test.insert(0, 'set', 'test')
        df_all = pd.concat([df_train, df_test], ignore_index=True)
        df_all = df_all[['set','text', 'label']]

        df_all.to_csv(output_file, index=False)

    print("Dataset Loaded")
    
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

def word_error_rate(original_arr, back_translated_arr):
    incorrect_words = {}
    correct = 0 
    overflow = 0

    for i in range(len(original_arr)):
        original_words = original_arr[i].split(" ") # gets all words from sentence of original
        back_words = back_translated_arr[i].split(" ")
        if len(back_words) > len(original_words):
            overflow += len(back_words) - len(original_words)

        for j in range(len(original_words)): # for all words in snetence compare to 
            if original_words[j] != back_words[j]:
                incorrect_words[original_words[j]] = back_words[j]
                correct += 1

    
    return incorrect_words, float(correct/(correct+len(incorrect_words)+overflow))

if __name__ == "__main__":
    test_text = ["The elections results are fake.", "I love soccer"]
    get_dataset()
    model,tokenizer,device = translate_init()
    test_translated = translate_text(test_text, model, tokenizer, device, "nso")
    test_back_translated = translate_text(test_translated, model, tokenizer, device, "eng")
    combined = []

    # for x,y in zip(test_text, test_back_translated):
    #     combined.append([x,y])

    out = jiwer.process_words(test_text, test_back_translated)
    print(jiwer.visualize_alignment(out))    
    # for i in range(len(test_text)):    
    #     w[i] = jiwer.wer(test_text[i], test_back_translated[i]) #word_error_rate(test_text, test_back_translated)
    
    #print(test_back_translated,w)
    # add 