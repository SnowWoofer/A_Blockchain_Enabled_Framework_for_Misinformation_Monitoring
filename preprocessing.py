import pandas as pd 

if __name__ == "__main__":
    

df = pd.read_csv("data/raw/twitter_data_nso_raw.csv", encoding='utf-8')
stats = df['text'].str.len().describe()
print(f"stats_count: {stats.count}", f"stats_mean: {stats.mean}", f"stats_std: {stats.std}", f"stats_min: {stats.std}", f"stats_min: {stats.min}")

