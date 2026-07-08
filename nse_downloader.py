import os
import sys
import datetime
import zipfile
import io
import time
import urllib3
import requests
import numpy as np
import pandas as pd
from scipy.special import ndtr
from tqdm import tqdm

# Suppress insecure request warnings if verify=False is needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Risk-free interest rate (7%)
RISK_FREE_RATE = 0.07

# Vectorized Black-Scholes pricing using scipy.special.ndtr (much faster)
def bs_price_vec(option_type, S, K, T, r, sigma):
    # Set small values to avoid division by zero
    T = np.maximum(T, 1e-5)
    sigma = np.maximum(sigma, 1e-5)
    
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    if option_type == 'CE':
        price = S * ndtr(d1) - K * np.exp(-r * T) * ndtr(d2)
    else:
        price = K * np.exp(-r * T) * ndtr(-d2) - S * ndtr(-d1)
    return price

# Vectorized Black-Scholes Delta calculation
def bs_delta_vec(option_type, S, K, T, r, sigma):
    d1 = np.where(
        T <= 0,
        np.where(S > K, 10.0, -10.0), # large values to make cdf go to 1 or 0
        (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (np.maximum(sigma, 1e-5) * np.sqrt(np.maximum(T, 1e-5)))
    )
    
    cdf = ndtr(d1)
    if option_type == 'CE':
        return cdf
    else:
        return cdf - 1.0

# Vectorized bisection solver for Implied Volatility
def solve_iv_vec(option_type, market_price, S, K, T, r, max_iter=20):
    N = len(S)
    low_vol = np.full(N, 0.0001)
    high_vol = np.full(N, 5.0)
    
    for _ in range(max_iter):
        mid_vol = (low_vol + high_vol) / 2.0
        price_mid = bs_price_vec(option_type, S, K, T, r, mid_vol)
        
        # update bounds
        high_vol = np.where(price_mid > market_price, mid_vol, high_vol)
        low_vol = np.where(price_mid > market_price, low_vol, mid_vol)
        
    final_vol = (low_vol + high_vol) / 2.0
    
    # If final_vol is too close to bounds, fallback to default IV of 25%
    is_valid = (final_vol > 0.001) & (final_vol < 4.9)
    final_vol = np.where(is_valid, final_vol, 0.25)
    
    return final_vol

# Master function to compute deltas
def get_deltas_vec(option_type, market_price, S, K, T, r):
    N = len(S)
    deltas = np.zeros(N)
    
    # Mask for valid rows to solve IV (need positive prices, spot, strike, and time)
    valid_mask = (~np.isnan(market_price)) & (market_price > 0) & (S > 0) & (K > 0) & (T > 0)
    
    vols = np.full(N, 0.25)
    if np.any(valid_mask):
        vols[valid_mask] = solve_iv_vec(
            option_type,
            market_price[valid_mask],
            S[valid_mask],
            K[valid_mask],
            T[valid_mask],
            r
        )
        
    # Calculate deltas with solved or default vols
    deltas = bs_delta_vec(option_type, S, K, T, r, vols)
    
    # Handle boundary case where T <= 0
    t_zero = T <= 0
    if np.any(t_zero):
        if option_type == 'CE':
            deltas[t_zero] = np.where(S[t_zero] > K[t_zero], 1.0, 0.0)
        else:
            deltas[t_zero] = np.where(S[t_zero] < K[t_zero], -1.0, 0.0)
            
    return deltas

def download_bhavcopy(date_obj, cache_dir, session):
    date_str = date_obj.strftime("%Y%m%d")
    filename = f"BhavCopy_NSE_FO_0_0_0_{date_str}_F_0000.csv.zip"
    filepath = os.path.join(cache_dir, filename)
    
    # Check cache
    if os.path.exists(filepath):
        return filepath, True
        
    url = f"https://nsearchives.nseindia.com/content/fo/{filename}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.nseindia.com/all-reports-derivatives",
    }
    
    try:
        response = session.get(url, headers=headers, timeout=10, verify=True)
        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return filepath, False
        elif response.status_code == 404:
            return None, False
        else:
            # Try once with verify=False just in case
            response = session.get(url, headers=headers, timeout=10, verify=False)
            if response.status_code == 200:
                with open(filepath, 'wb') as f:
                    f.write(response.content)
                return filepath, False
            return None, False
    except Exception:
        return None, False

def main():
    print("=" * 60)
    print(" NSE Derivatives Bhavcopy Downloader & Processor ")
    print("=" * 60)
    
    # Setup cache directory
    cache_dir = "nse_bhavcopy_cache"
    os.makedirs(cache_dir, exist_ok=True)
    
    # Generate dates for last 60 calendar days (ascending)
    today = datetime.date.today()
    dates = [today - datetime.timedelta(days=i) for i in range(60)]
    dates.sort()
    
    print(f"Checking for data from {dates[0]} to {dates[-1]} (60 calendar days)...")
    
    session = requests.Session()
    # Initial request to NSE website to establish cookies
    try:
        session.get("https://www.nseindia.com/all-reports-derivatives", headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }, timeout=10)
    except Exception as e:
        print(f"Warning: Could not establish session cookies: {e}. Will attempt direct download.")
        
    all_data = []
    
    print("\nDownloading and caching daily files:")
    for d in tqdm(dates, desc="Downloading Progress"):
        # Skip future dates just in case
        if d > today:
            continue
            
        # Skip Saturday/Sunday
        if d.weekday() >= 5:
            continue
            
        filepath, was_cached = download_bhavcopy(d, cache_dir, session)
        if filepath:
            try:
                with zipfile.ZipFile(filepath) as z:
                    for name in z.namelist():
                        if name.endswith(".csv"):
                            with z.open(name) as f:
                                df = pd.read_csv(f)
                                all_data.append(df)
            except Exception as e:
                print(f"\nError reading cached file {filepath}: {e}. Deleting corrupted file.")
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            # Add a small delay if downloading a new file
            if not was_cached:
                time.sleep(1.0)
                
    if not all_data:
        print("\n[ERROR] No data could be downloaded or retrieved. Please check your internet connection.")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    print("\nCombining data...")
    df_raw = pd.concat(all_data, ignore_index=True)
    
    # Basic cleaning
    df_raw.columns = [c.strip() for c in df_raw.columns]
    
    # Filter for STF/STO/IDF/IDO
    df_filtered = df_raw[df_raw['FinInstrmTp'].isin(['STF', 'STO', 'IDF', 'IDO'])].copy()
    
    if df_filtered.empty:
        print("[ERROR] No F&O data found in the downloaded bhavcopy files.")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    # Standardize types and strings
    df_filtered['TradDt'] = pd.to_datetime(df_filtered['TradDt']).dt.date
    df_filtered['XpryDt'] = pd.to_datetime(df_filtered['XpryDt']).dt.date
    df_filtered['TckrSymb'] = df_filtered['TckrSymb'].astype(str).str.strip()
    
    # Separate Futures (STF, IDF) and Options (STO, IDO)
    df_fut_raw = df_filtered[df_filtered['FinInstrmTp'].isin(['STF', 'IDF'])].copy()
    df_opt_raw = df_filtered[df_filtered['FinInstrmTp'].isin(['STO', 'IDO'])].copy()
    
    # Prepare Futures table
    df_fut = df_fut_raw[['TradDt', 'TckrSymb', 'XpryDt', 'ClsPric', 'PrvsClsgPric', 'OpnIntrst', 'ChngInOpnIntrst', 'TtlTradgVol']].copy()
    df_fut.rename(columns={
        'ClsPric': 'Future Price',
        'PrvsClsgPric': 'Prev Future Price',
        'OpnIntrst': 'Future OI',
        'ChngInOpnIntrst': 'Futures Change in OI',
        'TtlTradgVol': 'Futures Volume'
    }, inplace=True)
    
    # Prepare Options - separate CE and PE
    df_ce = df_opt_raw[df_opt_raw['OptnTp'] == 'CE'].copy()
    df_pe = df_opt_raw[df_opt_raw['OptnTp'] == 'PE'].copy()
    
    # CE options
    df_ce = df_ce[['TradDt', 'TckrSymb', 'XpryDt', 'StrkPric', 'UndrlygPric', 'ClsPric', 'OpnIntrst', 'ChngInOpnIntrst', 'TtlTradgVol']].copy()
    df_ce.rename(columns={
        'UndrlygPric': 'Spot Price',
        'ClsPric': 'Call Price',
        'OpnIntrst': 'Call OI',
        'ChngInOpnIntrst': 'Call Change in OI',
        'TtlTradgVol': 'Call Volume'
    }, inplace=True)
    
    # PE options
    df_pe = df_pe[['TradDt', 'TckrSymb', 'XpryDt', 'StrkPric', 'UndrlygPric', 'ClsPric', 'OpnIntrst', 'ChngInOpnIntrst', 'TtlTradgVol']].copy()
    df_pe.rename(columns={
        'UndrlygPric': 'Spot Price',
        'ClsPric': 'Put Price',
        'OpnIntrst': 'Put OI',
        'ChngInOpnIntrst': 'Put Change in OI',
        'TtlTradgVol': 'Put Volume'
    }, inplace=True)
    
    # Merge options on strike
    df_opt = pd.merge(
        df_ce,
        df_pe,
        on=['TradDt', 'TckrSymb', 'XpryDt', 'StrkPric'],
        how='outer',
        suffixes=('_ce', '_pe')
    )
    
    # Spot Price can come from either CE or PE
    df_opt['Spot Price'] = df_opt['Spot Price_ce'].fillna(df_opt['Spot Price_pe'])
    df_opt.drop(columns=['Spot Price_ce', 'Spot Price_pe'], inplace=True, errors='ignore')
    
    # Merge with Futures on key variables
    df_merged = pd.merge(
        df_fut,
        df_opt,
        on=['TradDt', 'TckrSymb', 'XpryDt'],
        how='right' # We keep options as rows
    )
    
    # Exclude rows where both Call OI and Put OI are zero or blank
    df_merged['Call OI'] = df_merged['Call OI'].fillna(0)
    df_merged['Put OI'] = df_merged['Put OI'].fillna(0)
    df_merged = df_merged[~((df_merged['Call OI'] == 0) & (df_merged['Put OI'] == 0))]
    
    if df_merged.empty:
        print("[WARNING] No rows remained after filtering out zero Call/Put OI.")
        input("\nPress Enter to exit...")
        sys.exit(0)
        
    df_merged.reset_index(drop=True, inplace=True)
    
    # Calculate Days to Expiry (T) in years
    trad_dates = pd.to_datetime(df_merged['TradDt'])
    xpry_dates = pd.to_datetime(df_merged['XpryDt'])
    T_days = (xpry_dates - trad_dates).dt.days
    T_years = T_days / 365.0
    T_years = np.maximum(T_years, 0.0) # Avoid negative time
    
    # Setup Spot price arrays
    S_vals = df_merged['Spot Price'].fillna(df_merged['Future Price']).fillna(0.0).values
    K_vals = df_merged['StrkPric'].fillna(0.0).values
    T_vals = T_years.values
    
    call_prices = df_merged['Call Price'].fillna(0.0).values
    put_prices = df_merged['Put Price'].fillna(0.0).values
    
    # Vectorized Delta Calculation
    print("Calculating Call/Put option deltas...")
    
    # Calculate Deltas
    call_deltas = get_deltas_vec('CE', call_prices, S_vals, K_vals, T_vals, RISK_FREE_RATE)
    put_deltas = get_deltas_vec('PE', put_prices, S_vals, K_vals, T_vals, RISK_FREE_RATE)
    
    df_merged['Call Delta'] = call_deltas
    df_merged['Put Delta'] = put_deltas
    
    # Recommendation Engine
    print("Analyzing option chain sentiment and generating recommendations...")
    # Add temporary columns for calculation
    df_merged['Call OI_temp'] = df_merged['Call OI'].fillna(0)
    df_merged['Put OI_temp'] = df_merged['Put OI'].fillna(0)
    
    # Compute PCR per group (TradDt, TckrSymb, XpryDt)
    group_oi = df_merged.groupby(['TradDt', 'TckrSymb', 'XpryDt'])[['Call OI_temp', 'Put OI_temp']].transform('sum')
    total_call_oi = group_oi['Call OI_temp']
    total_put_oi = group_oi['Put OI_temp']
    pcr = np.where(total_call_oi > 0, total_put_oi / total_call_oi, 0.0)
    
    # Futures price and OI change
    fut_price = df_merged['Future Price'].fillna(0.0)
    prev_fut_price = df_merged['Prev Future Price'].fillna(0.0)
    fut_price_change = fut_price - prev_fut_price
    fut_oi_change = df_merged['Futures Change in OI'].fillna(0.0)
    
    # Classify buildup
    has_fut = fut_price > 0
    is_long_buildup = has_fut & (fut_price_change > 0) & (fut_oi_change > 0)
    is_short_buildup = has_fut & (fut_price_change < 0) & (fut_oi_change > 0)
    is_short_covering = has_fut & (fut_price_change > 0) & (fut_oi_change < 0)
    is_long_unwinding = has_fut & (fut_price_change < 0) & (fut_oi_change < 0)
    
    # Determine the ATM Strike (strike closest to Future Price) for each group
    df_merged['strike_diff'] = np.abs(df_merged['StrkPric'] - fut_price)
    min_diff = df_merged.groupby(['TradDt', 'TckrSymb', 'XpryDt'])['strike_diff'].transform('min')
    is_atm = df_merged['strike_diff'] == min_diff
    
    # Recommendations
    recs = np.full(len(df_merged), "NEUTRAL", dtype=object)
    conf = np.full(len(df_merged), "N/A", dtype=object)
    
    # Bullish Recommendations
    bullish_mask = (is_long_buildup | is_short_covering) & is_atm
    recs[bullish_mask] = "BUY CALL"
    
    # Bearish Recommendations
    bearish_mask = (is_short_buildup | is_long_unwinding) & is_atm
    recs[bearish_mask] = "BUY PUT"
    
    # Set Confidence level
    # High Confidence (85%)
    high_conf_call = bullish_mask & is_long_buildup & (pcr > 1.1)
    high_conf_put = bearish_mask & is_short_buildup & (pcr < 0.9)
    conf[high_conf_call] = "High (85%)"
    conf[high_conf_put] = "High (85%)"
    
    # Medium Confidence (75% / 70%)
    med_conf_call_1 = bullish_mask & is_long_buildup & (pcr <= 1.1)
    med_conf_call_2 = bullish_mask & is_short_covering
    conf[med_conf_call_1] = "Medium (75%)"
    conf[med_conf_call_2] = "Medium (70%)"
    
    med_conf_put_1 = bearish_mask & is_short_buildup & (pcr >= 0.9)
    med_conf_put_2 = bearish_mask & is_long_unwinding
    conf[med_conf_put_1] = "Medium (75%)"
    conf[med_conf_put_2] = "Medium (70%)"
    
    # Low Confidence fallback for ATM signals
    low_conf_atm = is_atm & (recs != "NEUTRAL") & (conf == "N/A")
    conf[low_conf_atm] = "Low (60%)"
    
    df_merged['Recommendation'] = recs
    df_merged['Confidence Level'] = conf
    
    # Drop temp columns
    df_merged.drop(columns=['Call OI_temp', 'Put OI_temp', 'strike_diff', 'Prev Future Price'], inplace=True, errors='ignore')
    
    # Select final columns and order
    col_order = [
        'TradDt', 'TckrSymb', 'XpryDt',
        'Future Price', 'Future OI', 'Futures Change in OI', 'Futures Volume',
        'StrkPric',
        'Call OI', 'Call Price', 'Call Change in OI', 'Call Volume', 'Call Delta',
        'Put OI', 'Put Price', 'Put Change in OI', 'Put Volume', 'Put Delta',
        'Recommendation', 'Confidence Level'
    ]
    
    # Filter to make sure columns exist
    final_cols = [c for c in col_order if c in df_merged.columns]
    df_final = df_merged[final_cols].copy()
    
    # Rename headers to be user friendly
    header_mapping = {
        'TradDt': 'Trade Date',
        'TckrSymb': 'Symbol',
        'XpryDt': 'Expiry Date',
        'StrkPric': 'Strike Price'
    }
    df_final.rename(columns=header_mapping, inplace=True)
    
    # Create the 10 ITM OTM sheet using high-performance vectorised groupby
    print("Generating 10 ITM OTM data sheet (optimized)...")
    
    # Filter rows below or equal to Future Price
    df_below = df_final[df_final['Strike Price'] <= df_final['Future Price']].copy()
    df_below = df_below.sort_values(by=['Trade Date', 'Symbol', 'Expiry Date', 'Strike Price'], ascending=[True, True, True, False])
    df_below_10 = df_below.groupby(['Trade Date', 'Symbol', 'Expiry Date']).head(10)
    
    # Filter rows above Future Price
    df_above = df_final[df_final['Strike Price'] > df_final['Future Price']].copy()
    df_above = df_above.sort_values(by=['Trade Date', 'Symbol', 'Expiry Date', 'Strike Price'], ascending=[True, True, True, True])
    df_above_10 = df_above.groupby(['Trade Date', 'Symbol', 'Expiry Date']).head(10)
    
    # Combine and sort
    df_itm_otm = pd.concat([df_below_10, df_above_10], ignore_index=True)
    df_itm_otm.sort_values(by=['Trade Date', 'Symbol', 'Expiry Date', 'Strike Price'], inplace=True)
    df_itm_otm.reset_index(drop=True, inplace=True)
    
    # Determine the highlight row indices in pandas (extremely fast)
    print("Calculating highlights for nearest 3 ITM/OTM strikes...")
    df_itm_otm['is_below'] = df_itm_otm['Strike Price'] <= df_itm_otm['Future Price']
    df_itm_otm['rank'] = np.nan
    
    below_mask = df_itm_otm['is_below']
    # Rank strikes below (1 is closest to future price)
    df_itm_otm.loc[below_mask, 'rank'] = df_itm_otm[below_mask].groupby(['Trade Date', 'Symbol', 'Expiry Date'])['Strike Price'].rank(ascending=False, method='first')
    # Rank strikes above (1 is closest to future price)
    df_itm_otm.loc[~below_mask, 'rank'] = df_itm_otm[~below_mask].groupby(['Trade Date', 'Symbol', 'Expiry Date'])['Strike Price'].rank(ascending=True, method='first')
    
    # Highlight if rank <= 3
    df_itm_otm['_highlight'] = df_itm_otm['rank'] <= 3
    highlight_rows = np.where(df_itm_otm['_highlight'].values)[0] + 1 # xlsxwriter: row 0 is header, so row i of df is row i+1 in Excel
    
    # Clean up temp columns
    df_itm_otm.drop(columns=['is_below', 'rank', '_highlight'], inplace=True, errors='ignore')
    
    # Output Excel File path
    output_filename = "NSE_Derivatives_Combined_60Days.xlsx"
    print(f"Writing data to Excel: {output_filename} (using XlsxWriter)...")
    sys.stdout.flush()
    
    # Write to Excel using pandas and xlsxwriter
    writer = pd.ExcelWriter(output_filename, engine='xlsxwriter')
    df_final.to_excel(writer, sheet_name='Combined Data', index=False)
    df_itm_otm.to_excel(writer, sheet_name='10 ITM OTM', index=False)
        
    # Styling - Highlighting 3 ITM and 3 OTM contracts in yellow
    print("Applying styling and highlighting 3 ITM and OTM strikes...")
    sys.stdout.flush()
    workbook = writer.book
    worksheet = writer.sheets['10 ITM OTM']
    
    yellow_format = workbook.add_format({'bg_color': '#FFFF00'})
    
    # Apply row highlight using xlsxwriter row formatting
    for r_num in highlight_rows:
        worksheet.set_row(int(r_num), None, yellow_format)
            
    writer.close()
    
    print(f"Successfully processed {len(df_final)} total rows.")
    print(f"Excel workbook created: {output_filename}")
    print("-" * 60)
    print("Done! You can open the Excel sheet now.")
    print("-" * 60)

if __name__ == '__main__':
    main()
