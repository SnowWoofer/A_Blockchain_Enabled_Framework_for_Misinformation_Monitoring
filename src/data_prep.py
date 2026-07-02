import pandas as pd 
from datasets import load_dataset
import os

def get_dataset():
    
    output_dir = "data/raw"
    output_file = os.path.join(output_dir, "twitter_data_english.csv")

    if not os.path.exists(output_dir): # if the dir dosenst exist, then make it, also implies no output file exists
        os.makedirs(output_dir)

        
    if not os.path.exists(output_file) or os.path.getsize(output_file) == 200:   #get the dataset agin in not existaneat or wrong size
        ds = load_dataset("roupenminassian/twitter-misinformation")

        df_train = ds["train"].to_pandas()
        df_test = ds["test"].to_pandas()
        df_all = pd.concat([df_train, df_test], keys=['train', 'test'], ignore_index=False)

        print(df_all)
        df_all.to_csv(output_file)

def translate():
    
    
    print("Hey")

if __name__ == "__main__":
    get_dataset()