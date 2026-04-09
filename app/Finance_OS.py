import os
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf
import requests
import urllib3
from datetime import datetime
from dateutil.relativedelta import relativedelta
import plotly.express as px
import plotly.graph_objects as go
import datetime as dt_lib
import warnings
import logging

# Ρύθμιση του logger
logging.basicConfig(
    filename='app.log', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

warnings.filterwarnings('ignore', category=UserWarning)

# 1. Απενεργοποίηση των SSL Warnings (για να μην γεμίζουν τα logs)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. Δημιουργία session που ΑΓΝΟΕΙ το SSL verification
session = requests.Session()
session.verify = False  # <--- ΑΥΤΟ ΕΙΝΑΙ ΤΟ ΚΛΕΙΔΙ


# --- CONFIG & DB CONNECTION ---
def get_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "Finance"),
        user=os.getenv("DB_USER", "admin"),
        password=os.getenv("DB_PASSWORD", "password"),
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5432")
    )

def update_selection():
    # Παίρνουμε το selection από το widget και το αποθηκεύουμε μόνιμα
    key = f"set_reg_{acc_id}"
    if key in st.session_state:
        st.session_state[f"last_selection_{acc_id}"] = st.session_state[key].get("selection", {}).get("rows", [])

def capture_selection(key, acc_id):
    # Μεταφέρουμε την επιλογή από το widget state σε μια σταθερή μεταβλητή
    if key in st.session_state:
        sel = st.session_state[key].get("selection", {}).get("rows", [])
        st.session_state[f"active_sel_{acc_id}"] = sel

def handle_selection(key, storage_key):
    if key in st.session_state:
        # Παίρνουμε το selection και το αποθηκεύουμε σε δικό μας κλειδί
        selection = st.session_state[key].get("selection", {}).get("rows", [])
        st.session_state[storage_key] = selection


def save_changes_no_serial(df_original, df_edited, table_name, id_col):
    if st.button(f"💾 Save {table_name}"):
        conn = get_connection()
        cur = conn.cursor()
        try:

            # 1. ΕΝΤΟΠΙΣΜΟΣ ΚΑΙ ΔΙΑΓΡΑΦΗ ΟΣΩΝ ΛΕΙΠΟΥΝ
            if table_name == "Historical_FX":
                # Χρήση | ως διαχωριστικό γιατί η ημερομηνία έχει παύλες
                def get_keys(df):
                    return set(df.apply(lambda r: f"{int(r['base_currency_id'])}|{int(r['target_currency_id'])}|{r['fx_date']}", axis=1))
                
                original_keys = get_keys(df_original)
                edited_keys = get_keys(df_edited)
                keys_to_delete = original_keys - edited_keys

                for key in keys_to_delete:
                    # Τώρα το split('|') θα επιστρέψει ακριβώς 3 κομμάτια
                    b_id, t_id, f_date = key.split('|')
                    cur.execute(f"""
                        DELETE FROM {table_name} 
                        WHERE base_currency_id = %s 
                        AND target_currency_id = %s 
                        AND fx_date = %s
                    """, (int(b_id), int(t_id), f_date))

        
            # 2. UPDATES / INSERTS (Ο υπάρχων κώδικάς σου)
            cols = df_edited.columns.tolist()
            data_tuples = [tuple(None if pd.isna(v) else v for v in row) for row in df_edited.values]

            if table_name == "Historical_FX":
                conflict_target = "base_currency_id, target_currency_id, fx_date"
                update_cols = ["fx_rate"]
            else:
                conflict_target = id_col
                update_cols = [c for c in cols if c != id_col]

            update_stmt = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
            sql = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT ({conflict_target}) DO UPDATE SET {update_stmt}"
            
            execute_values(cur, sql, data_tuples)
            
            conn.commit()
            st.success("Οι αλλαγές αποθηκεύτηκαν!")
            st.rerun()
        except Exception as e:
            conn.rollback()
            st.error(f"Σφάλμα: {e}")
        finally:
            conn.close()

def save_changes(df_original, df_edited, table_name, id_col, current_acc_id=None):
    """
    df_original: Το DataFrame όπως διαβάστηκε από τη DB (πριν τον editor)
    df_edited: Το DataFrame που επιστρέφει ο st.data_editor
    """
    if st.button(f"💾 Save {table_name}"):
        conn = get_connection()
        cur = conn.cursor()
        try:
            # 1. ΕΝΤΟΠΙΣΜΟΣ ΔΙΑΓΡΑΦΩΝ
            original_ids = set(df_original[id_col].dropna().unique())
            edited_ids = set(df_edited[id_col].dropna().unique())
            # Μετατροπή των IDs σε απλά Python ints για να μην χτυπάει η psycopg2
            ids_to_delete = [int(x) for x in (original_ids - edited_ids)]

            if ids_to_delete:
                # Χρησιμοποιούμε tuple(ids_to_delete) για το query
                cur.execute(f"DELETE FROM {table_name} WHERE {id_col} IN %s", (tuple(ids_to_delete),))

            # 2. ΔΙΑΧΩΡΙΣΜΟΣ ΝΕΩΝ ΚΑΙ UPDATES
            df_new = df_edited[df_edited[id_col].isna()].copy()
            df_updates = df_edited[df_edited[id_col].notna()].copy()

            # 3. ΕΚΤΕΛΕΣΗ UPDATES (INSERT ... ON CONFLICT)
        #    if not df_updates.empty:
        #        # Η astype(object) μετατρέπει τα numpy types σε python types
        #        data_tuples = [
        #            tuple(None if pd.isna(v) else v for v in row) 
        #            for row in df_updates.astype(object).values.tolist()
        #        ]
        #        cols = df_updates.columns.tolist()
        #        data_tuples = [tuple(None if pd.isna(v) else v for v in row) for row in df_updates.values]
        #        update_cols = [c for c in cols if c != id_col]
        #        update_stmt = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
        #        
        #        sql_upd = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT ({id_col}) DO UPDATE SET {update_stmt}"
        #        execute_values(cur, sql_upd, data_tuples)

            # 3. ΕΚΤΕΛΕΣΗ UPDATES (Κανονικό UPDATE αντί για ON CONFLICT)
            if not df_updates.empty:
                cols = df_updates.columns.tolist()
                update_cols = [c for c in cols if c != id_col]
                
                # Δημιουργία δυναμικού query: UPDATE table SET col1=%s, col2=%s WHERE id=%s
                set_clause = ", ".join([f"{c} = %s" for c in update_cols])
                sql_upd = f"UPDATE {table_name} SET {set_clause} WHERE {id_col} = %s"
                
                for _, row in df_updates.iterrows():
                    # Παίρνουμε τις τιμές για το SET και στο τέλος το ID για το WHERE
                    vals = [None if pd.isna(row[c]) else row[c] for c in update_cols]
                    vals.append(int(row[id_col]))
                    cur.execute(sql_upd, tuple(vals))

            # 4. ΕΚΤΕΛΕΣΗ INSERTS (Χωρίς το id_col)
            if not df_new.empty:
                df_new_to_insert = df_new.drop(columns=[id_col])
                data_tuples_new = [
                    tuple(None if pd.isna(v) else v for v in row) 
                    for row in df_new_to_insert.astype(object).values.tolist()
                ]
                df_new_to_insert = df_new.drop(columns=[id_col])
                cols_new = df_new_to_insert.columns.tolist()
                data_tuples_new = [tuple(None if pd.isna(v) else v for v in row) for row in df_new_to_insert.values]

                sql_ins = f"INSERT INTO {table_name} ({', '.join(cols_new)}) VALUES %s"
                execute_values(cur, sql_ins, data_tuples_new)

            conn.commit()

            # Ενημέρωση ΜΟΝΟ για τον συγκεκριμένο λογαριασμό
            if table_name == "Bank_Transactions" and current_acc_id:
                update_account_balances(current_acc_id)
                
            st.success(f"Αποθηκεύτηκαν: {len(df_updates)} ενημερώσεις, {len(df_new)} νέες, {len(ids_to_delete)} διαγραφές")
            st.rerun()

        except Exception as e:
            conn.rollback()
            st.error(f"Σφάλμα: {e}")
        finally:
            conn.close()

def commit_changes(df_original, df_edited, table_name, id_col):
    """
    df_original: Το DataFrame όπως διαβάστηκε από τη DB (πριν τον editor)
    df_edited: Το DataFrame που επιστρέφει ο st.data_editor
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. ΕΝΤΟΠΙΣΜΟΣ ΔΙΑΓΡΑΦΩΝ
        original_ids = set(df_original[id_col].dropna().unique())
        edited_ids = set(df_edited[id_col].dropna().unique())
        # Μετατροπή των IDs σε απλά Python ints για να μην χτυπάει η psycopg2
        ids_to_delete = [int(x) for x in (original_ids - edited_ids)]

        if ids_to_delete:
            # Χρησιμοποιούμε tuple(ids_to_delete) για το query
            cur.execute(f"DELETE FROM {table_name} WHERE {id_col} IN %s", (tuple(ids_to_delete),))

        # 2. ΔΙΑΧΩΡΙΣΜΟΣ ΝΕΩΝ ΚΑΙ UPDATES
        df_new = df_edited[df_edited[id_col].isna()].copy()
        df_updates = df_edited[df_edited[id_col].notna()].copy()

        # 3. ΕΚΤΕΛΕΣΗ UPDATES (INSERT ... ON CONFLICT)
        if not df_updates.empty:
            # Η astype(object) μετατρέπει τα numpy types σε python types
            data_tuples = [
                tuple(None if pd.isna(v) else v for v in row) 
                for row in df_updates.astype(object).values.tolist()
            ]
            cols = df_updates.columns.tolist()
            data_tuples = [tuple(None if pd.isna(v) else v for v in row) for row in df_updates.values]
            update_cols = [c for c in cols if c != id_col]
            update_stmt = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
            
            sql_upd = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES %s ON CONFLICT ({id_col}) DO UPDATE SET {update_stmt}"
            execute_values(cur, sql_upd, data_tuples)

        # 4. ΕΚΤΕΛΕΣΗ INSERTS (Χωρίς το id_col)
        if not df_new.empty:
            df_new_to_insert = df_new.drop(columns=[id_col])
            data_tuples_new = [
                tuple(None if pd.isna(v) else v for v in row) 
                for row in df_new_to_insert.astype(object).values.tolist()
            ]
            df_new_to_insert = df_new.drop(columns=[id_col])
            cols_new = df_new_to_insert.columns.tolist()
            data_tuples_new = [tuple(None if pd.isna(v) else v for v in row) for row in df_new_to_insert.values]

            sql_ins = f"INSERT INTO {table_name} ({', '.join(cols_new)}) VALUES %s"
            execute_values(cur, sql_ins, data_tuples_new)

        conn.commit()
        st.success(f"Αποθηκεύτηκαν: {len(df_updates)} ενημερώσεις, {len(df_new)} νέες, {len(ids_to_delete)} διαγραφές")
        st.rerun()

    except Exception as e:
        conn.rollback()
        st.error(f"Σφάλμα: {e}")
    finally:
        conn.close()


def save_changes_mid(df_edited, table_name, id_cols, filter_col=None, filter_val=None):
    """
    Unique Key consists of multiple IDs (e.g., for Historical_Prices, it is the combination of Securities_Id & Price_Date
    id_cols: Λίστα με τις στήλες που αποτελούν το Unique Key (π.χ. ['securities_id', 'price_date'])
    filter_col/filter_val: Χρησιμοποιείται για να σβήνουμε μόνο τις εγγραφές του συγκεκριμένου Security
    """
    if st.button(f"💾 Save {table_name}"):
        conn = get_connection()
        cur = conn.cursor()
        try:
            # 1. Διαγραφή παλιών εγγραφών ΜΟΝΟ για το συγκεκριμένο Security (αν οριστεί φίλτρο)
            if filter_col and filter_val:
                # Κρατάμε μόνο όσα υπάρχουν στο editor για το συγκεκριμένο security
                current_dates = df_edited['price_date'].dropna().tolist()
                if current_dates:
                    cur.execute(f"DELETE FROM {table_name} WHERE {filter_col} = %s AND price_date NOT IN %s", 
                                (filter_val, tuple(current_dates)))
                else:
                    cur.execute(f"DELETE FROM {table_name} WHERE {filter_col} = %s", (filter_val,))

            # 2. Upsert
            for _, row in df_edited.iterrows():
                # Σιγουρευόμαστε ότι το securities_id είναι συμπληρωμένο
                if filter_col and filter_val:
                    row[filter_col] = filter_val
                
                cols = row.index.tolist()
                vals = [None if pd.isna(v) else v for v in row.values]
                
                placeholders = ", ".join(["%s"] * len(cols))
                # Το update γίνεται για όλες τις στήλες εκτός των κλειδιών
                update_cols = [c for c in cols if c not in id_cols]
                update_stmt = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
                
                conflict_target = ", ".join(id_cols)
                
                sql = f"""
                    INSERT INTO {table_name} ({', '.join(cols)}) 
                    VALUES ({placeholders}) 
                    ON CONFLICT ({conflict_target}) 
                    DO UPDATE SET {update_stmt}
                """
                cur.execute(sql, vals)

            conn.commit()
            st.success("Changes saved!")
            st.rerun()
        except Exception as e:
            conn.rollback()
            st.error(f"Error: {e}")
        finally:
            conn.close()


def update_account_balances_OLD():
    conn = get_connection()
    cur = conn.cursor()
    
    # --- 2. ΜΑΖΙΚΗ ΕΝΗΜΕΡΩΣΗ ΥΠΟΛΟΙΠΩΝ FROM BANK TRANSACTIONS ---
    try:
        cur.execute("""
            UPDATE Accounts a
            SET Account_Balance = COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Bank_Transactions t 
                WHERE t.Accounts_Id = a.Accounts_Id
            ), 0)
			WHERE a.Accounts_Type NOT IN ('Pension');
        """)
        conn.commit()
    except Exception as e:
        st.error(f"❌ Σφάλμα: {e}")
    finally:
        cur.close()
        conn.close()

def update_account_balances(target_acc_id=None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        if target_acc_id:
            # Χρήση διπλών εισαγωγικών για διατήρηση του Case
            sql = """
                UPDATE Accounts a
                SET Account_Balance = COALESCE((
                    SELECT SUM(Total_Amount) 
                    FROM Bank_Transactions t 
                    WHERE t.Accounts_Id = a.Accounts_Id
                ), 0)
                WHERE a.Accounts_Id = %s;
            """
            cur.execute(sql, (int(target_acc_id),))
        else:
            sql = """
                UPDATE Accounts a
                SET Account_Balance = COALESCE((
                    SELECT SUM(Total_Amount) 
                    FROM Bank_Transactions t 
                    WHERE t.Accounts_Id = a.Accounts_Id
                ), 0)
                WHERE a.Accounts_Type NOT IN ('Pension');
            """
            cur.execute(sql)
        conn.commit()
    except Exception as e:
        st.error(f"❌ Σφάλμα: {e}")
    finally:
        cur.close()
        conn.close()

def update_pension_balances():
    conn = get_connection()
    cur = conn.cursor()
    
    # --- 2. ΜΑΖΙΚΗ ΕΝΗΜΕΡΩΣΗ ΥΠΟΛΟΙΠΩΝ FROM PENSION TRANSACTIONS ---
    try:
        cur.execute("""
            UPDATE Accounts a
			SET Account_Balance = COALESCE((
                SELECT  
                    SUM(CASE WHEN Action IN ('CashIn', 'IntInc') THEN Total_Amount 
                             WHEN Action IN ('CashOut') THEN -Total_Amount 
                             ELSE 0 END)
                FROM Investment_Transactions t 
                WHERE t.Accounts_Id = a.Accounts_Id
            ), 0)
			WHERE a.Accounts_Type IN ('Pension');
        """)
        conn.commit()
    except Exception as e:
        st.error(f"❌ Σφάλμα: {e}")
    finally:
        cur.close()
        conn.close()


def update_holdings():
    conn = get_connection()
    cur = conn.cursor()
    
    # --- 2. ΜΑΖΙΚΗ ΕΝΗΜΕΡΩΣΗ ΥΠΟΛΟΙΠΩΝ HOLDINGS ---
    try:
        cur.execute("""
            INSERT INTO Holdings (Accounts_Id, Securities_Id, Quantity, Avg_Purchase_Price)
            SELECT 
                Accounts_Id, 
                Securities_Id, 
                SUM(CASE WHEN Action IN ('Buy', 'Reinvest', 'ShrIn') THEN Quantity 
                         WHEN Action IN ('Sell', 'ShrOut') THEN -Quantity 
                         ELSE 0 END),
                AVG(Price_Per_Share) FILTER (WHERE Action IN ('Buy', 'Reinvest', 'ShrIn'))
            FROM Investment_Transactions
            GROUP BY Accounts_Id, Securities_Id
            ON CONFLICT (Accounts_Id, Securities_Id) 
            DO UPDATE SET 
                Quantity = EXCLUDED.Quantity,
                Avg_Purchase_Price = EXCLUDED.Avg_Purchase_Price,
                Last_Update = CURRENT_TIMESTAMP;
        """)
        conn.commit()
    except Exception as e:
        st.error(f"❌ Σφάλμα: {e}")
    finally:
        cur.close()
        conn.close()    
    
def download_historical_fx(tsperiod):
     conn = get_connection()
     cur = conn.cursor()

     try:
         # 2. Εύρεση του EUR ID και όλων των άλλων ενεργών νομισμάτων
         cur.execute("SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR'")
         target_id = cur.fetchone()[0]

         cur.execute("SELECT Currencies_Id, Currencies_ShortName FROM Currencies WHERE Currencies_ShortName != 'EUR'")
         currencies = cur.fetchall()

         for base_id, symbol in currencies:
             #st.write(f"📥 Λήψη ιστορικών δεδομένων για {symbol}...")
             logging.info(f"Λήψη ιστορικών δεδομένων για {symbol}...")

             # Yahoo Ticker format: EURUSD=X (δίνει 1 EUR = X USD)
             ticker_symbol = f"EUR{symbol}=X"
             logging.info(f"EUR{symbol}=X")
             ticker = yf.Ticker(ticker_symbol)

             # Κατεβάζουμε δεδομένα 5 ετών (ή 'max' για όλα)
             hist = ticker.history(period=tsperiod)

             if hist.empty:
                 st.warning(f"⚠ Δεν βρέθηκαν δεδομένα για το {ticker_symbol}")
                 logging.info(f"Δεν βρέθηκαν δεδομένα για το {ticker_symbol}")
                 continue

             # 3. Προετοιμασία δεδομένων για μαζική εισαγωγή (Bulk Insert)
             for date, row in hist.iterrows():
                 # Αντιστροφή: Θέλουμε 1 USD = πόσα EUR
                 # rate_to_eur = 1 / (πόσα USD κάνει 1 EUR)
                 rate_to_eur = float(1 / row['Close'])
                 formatted_date = date.strftime('%Y-%m-%d')

                 cur.execute("""
                     INSERT INTO Historical_FX (Base_Currency_Id, Target_Currency_Id, FX_Date, FX_Rate)
                     VALUES (%s, %s, %s, %s)
                     ON CONFLICT (Base_Currency_Id, Target_Currency_Id, FX_Date)
                     DO UPDATE SET FX_Rate = EXCLUDED.FX_Rate
                 """, (base_id, target_id, formatted_date, rate_to_eur))

             conn.commit()
             #st.success(f"✅ Ολοκληρώθηκε η εισαγωγή για {symbol}")
             logging.info(f"Ολοκληρώθηκε η εισαγωγή για {symbol}")

     except Exception as e:
         st.error(f"❌ Σφάλμα: {e}")
         logging.info(f"Σφάλμα: {e}")
     finally:
         cur.close()
         conn.close()

def download_historical_prices_from_yahoo(tsperiod):
     conn = get_connection()
     cur = conn.cursor()

     try:
         # 2. Εύρεση του EUR ID και όλων των άλλων ενεργών νομισμάτων
   #      cur.execute("SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR'")
   #      target_id = cur.fetchone()[0]

         #cur.execute("SELECT Securities_Id, Security_Name, Yahoo_Ticker FROM Securities WHERE Yahoo_Ticker IS NOT NULL AND Security_Name NOT LIKE 'Hellenic T-Bill%' ORDER BY Security_Name ASC")
         cur.execute("""
            SELECT Securities_Id, Security_Name, Yahoo_Ticker 
            FROM Securities 
            WHERE Yahoo_Ticker IS NOT NULL 
            AND Yahoo_Ticker != '' 
            AND Security_Name NOT LIKE 'Hellenic T-Bill%' 
            ORDER BY Security_Name ASC
         """)

         securities = cur.fetchall()

         for sec_id, sec_name, symbol in securities:
             #st.write(f"📥 Λήψη ιστορικών δεδομένων για {sec_name}...")
             logging.info(f"Λήψη ιστορικών δεδομένων για {sec_name}...")

             # Yahoo Ticker format: EURUSD=X (δίνει 1 EUR = X USD)
    #         ticker_symbol = f"EUR{symbol}=X"
             ticker_symbol = symbol
             ticker = yf.Ticker(ticker_symbol)

             # Κατεβάζουμε δεδομένα 5 ετών (ή 'max' για όλα)
             hist = ticker.history(period=tsperiod)

             if hist.empty:
                 st.warning(f"⚠ Δεν βρέθηκαν δεδομένα για το {sec_name}")
                 logging.info(f"Δεν βρέθηκαν δεδομένα για το {sec_name}")
                 continue

             # 3. Προετοιμασία δεδομένων για μαζική εισαγωγή (Bulk Insert)
             for date, row in hist.iterrows():
                 rate = float(row['Close'])
                 volume = float(row['Volume'])
                 formatted_date = date.strftime('%Y-%m-%d')

                 cur.execute("""
                     INSERT INTO Historical_Prices (Securities_Id, Price_Date, Price_Close, Volume)
                     VALUES (%s, %s, %s, %s)
                     ON CONFLICT (Securities_Id, Price_Date)
                     DO UPDATE SET Price_Close = EXCLUDED.Price_Close, Volume = EXCLUDED.Volume
                 """, (sec_id, formatted_date, rate, volume))

             conn.commit()
             #st.success(f"✅ Ολοκληρώθηκε η εισαγωγή για {symbol}")
             logging.info(f"Ολοκληρώθηκε η εισαγωγή για {symbol}")

     except Exception as e:
         st.error(f"❌ Σφάλμα: {e}")
         logging.info(f"Σφάλμα: {e}")
     finally:
         cur.close()
         conn.close()

@st.cache_data(ttl=3600) # Cache για 1 ώρα
def get_hist_net_worth_data(start_date):
    conn = get_connection()
    query = f""" ... (εδώ το SQL query από τις σελίδες 18-20 του PDF) ... """
    
    query_history_monthly = f"""
    WITH RECURSIVE 
    months AS (
        -- Ξεκινάμε από το τέλος του τριμήνου της ημερομηνίας έναρξης
        SELECT (date_trunc('month', '{start_date}'::date) + INTERVAL '1 month' - INTERVAL '1 day')::date as d
        UNION ALL
        -- Προσθέτουμε 3 μήνες και υπολογίζουμε το τέλος του επόμενου τριμήνου
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date 
        FROM months 
        WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (
        -- Παίρνουμε όλες τις τελευταίες ημέρες των τριμήνων που είναι μικρότερες ή ίσες με σήμερα
        SELECT d FROM months WHERE d <= CURRENT_DATE
        UNION
        -- Προσθέτουμε και τη σημερινή ημερομηνία για up-to-date εικόνα
        SELECT CURRENT_DATE::date
    ),
    -- Υπολογισμός Cash Balance (Σημερινό - κινήσεις μετά την ημερομηνία D)
    historical_assets AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Account_Balance - COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Bank_Transactions 
                WHERE Accounts_Id = a.Accounts_Id 
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Real Estate', 'Vehicle', 'Asset', 'Liability')
--            WHERE a.Accounts_Type NOT IN ('Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Other')
    ),
    historical_cash AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Account_Balance - COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Bank_Transactions 
                WHERE Accounts_Id = a.Accounts_Id 
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
--          WHERE a.Accounts_Type NOT IN ('Brokerage', 'Pension', 'Other Investment', 'Margin', 'Real Estate', 'Vehicle', 'Asset', 'Liability')
        WHERE a.Accounts_Type IN ('Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Other')
    ),
    -- Υπολογισμός Pension Balance (Σημερινό - κινήσεις μετά την ημερομηνία D)
    historical_pension AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Account_Balance - COALESCE((
                SELECT  
                    SUM(CASE WHEN Action IN ('CashIn', 'IntInc') THEN Total_Amount 
                             WHEN Action IN ('CashOut') THEN -Total_Amount 
                             ELSE 0 END)
                FROM Investment_Transactions
                WHERE Accounts_Id = a.Accounts_Id
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
--            WHERE a.Is_Active = TRUE AND a.Accounts_Type IN ('Pension')
        WHERE a.Accounts_Type IN ('Pension')
    ),
    -- Υπολογισμός Holdings (Σημερινή ποσότητα - κινήσεις μετά την ημερομηνία D)
    historical_inv AS (
        SELECT 
            dt.d as date,
            h.Securities_Id,
            h.Quantity - COALESCE((
                SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END)
                FROM Investment_Transactions 
                WHERE Securities_Id = h.Securities_Id 
                AND Date > dt.d
            ), 0) as qty_at_date
        FROM dates dt
        CROSS JOIN Holdings h
    ),
    daily_fx AS (
        SELECT dt.d as date, c.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE FX_Date <= dt.d AND Base_Currency_Id = c.Currencies_Id ORDER BY FX_Date DESC LIMIT 1) as fx_rate
        FROM dates dt CROSS JOIN Currencies c
    ),
    daily_prices AS (
        SELECT dt.d as date, s.Securities_Id,
            (SELECT Price_Close FROM Historical_Prices WHERE Price_Date <= dt.d AND Securities_Id = s.Securities_Id ORDER BY Price_Date DESC LIMIT 1) as price_close
        FROM dates dt CROSS JOIN Securities s
    ),
    -- ... (τα προηγούμενα CTEs quarters, dates, historical_... παραμένουν ως έχουν) ...
    
    final_calculation AS (
        SELECT 
            dt.d as date,
            -- Υπολογισμός Assets
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN ha.balance_at_date 
                ELSE ha.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_assets ha
             JOIN Currencies cur ON ha.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON ha.date = dfx.date AND ha.Currencies_Id = dfx.Currencies_Id
             WHERE ha.date = dt.d) as total_assets,

            -- Υπολογισμός Cash
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN hc.balance_at_date 
                ELSE hc.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_cash hc
             JOIN Currencies cur ON hc.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hc.date = dfx.date AND hc.Currencies_Id = dfx.Currencies_Id
             WHERE hc.date = dt.d) as total_cash,

            -- Υπολογισμός Pension
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN hp.balance_at_date 
                ELSE hp.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_pension hp
             JOIN Currencies cur ON hp.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hp.date = dfx.date AND hp.Currencies_Id = dfx.Currencies_Id
             WHERE hp.date = dt.d) as total_pension,

            -- Υπολογισμός Invested
            (SELECT SUM(hi.qty_at_date * COALESCE(dp.price_close, 0) * 
                CASE WHEN cs.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(dfx_inv.fx_rate, 1) END
             )
             FROM historical_inv hi
             JOIN Securities s ON hi.Securities_Id = s.Securities_Id
             JOIN Currencies cs ON s.Currencies_Id = cs.Currencies_Id
             LEFT JOIN daily_prices dp ON hi.date = dp.date AND hi.Securities_Id = dp.Securities_Id
             LEFT JOIN daily_fx dfx_inv ON hi.date = dfx_inv.date AND s.Currencies_Id = dfx_inv.Currencies_Id
             WHERE hi.date = dt.d) as total_invested

        FROM dates dt
    )
    SELECT 
        date,
        COALESCE(total_assets, 0) as total_assets,
        COALESCE(total_cash, 0) as total_cash,
        COALESCE(total_pension, 0) as total_pension,
        COALESCE(total_invested, 0) as total_invested,
        (COALESCE(total_assets, 0) + COALESCE(total_cash, 0) + COALESCE(total_pension, 0) + COALESCE(total_invested, 0)) as total_net_worth
    FROM final_calculation
    ORDER BY date ASC

    """
    
    df = pd.read_sql(query_history_monthly, conn)
    conn.close()
    return df

@st.cache_data(ttl=3600)
def get_hist_inv_positions_data(start_date):
    conn = get_connection()
    query = f""" ... (εδώ το SQL query από τις σελίδες 23-24 του PDF) ... """

    query_history_monthly = f"""
    WITH RECURSIVE 
    months AS (
        SELECT (date_trunc('month', '{start_date}'::date) + INTERVAL '1 month' - INTERVAL '1 day')::date as d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date 
        FROM months 
        WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (
        SELECT d FROM months WHERE d <= CURRENT_DATE
        UNION
        SELECT CURRENT_DATE::date
    ),
    -- Υπολογισμός ποσότητας ανά Security και Ημερομηνία
    historical_qty AS (
        SELECT 
            dt.d as date,
            h.Securities_Id,
            h.Accounts_Id,
            h.Quantity - COALESCE((
                SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END)
                FROM Investment_Transactions 
                WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id
                AND Date > dt.d
            ), 0) as qty_at_date
        FROM dates dt
        CROSS JOIN Holdings h
    ),
    daily_fx AS (
        SELECT dt.d as date, c.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE FX_Date <= dt.d AND Base_Currency_Id = c.Currencies_Id ORDER BY FX_Date DESC LIMIT 1) as fx_rate
        FROM dates dt CROSS JOIN Currencies c
    ),
    daily_prices AS (
        SELECT dt.d as date, s.Securities_Id,
            (SELECT Price_Close FROM Historical_Prices WHERE Price_Date <= dt.d AND Securities_Id = s.Securities_Id ORDER BY Price_Date DESC LIMIT 1) as price_close
        FROM dates dt CROSS JOIN Securities s
    )
/*    
    -- Τελικός υπολογισμός ανά λογαριασμό
    SELECT 
        hq.date,
        a.Accounts_Name,
        SUM(hq.qty_at_date * COALESCE(dp.price_close, 0) * 
            CASE WHEN cur_s.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(dfx.fx_rate, 1) END
        ) as account_value
    FROM historical_qty hq
    JOIN Accounts a ON hq.Accounts_Id = a.Accounts_Id
    JOIN Securities s ON hq.Securities_Id = s.Securities_Id
    JOIN Currencies cur_s ON s.Currencies_Id = cur_s.Currencies_Id
    LEFT JOIN daily_prices dp ON hq.date = dp.date AND hq.Securities_Id = dp.Securities_Id
    LEFT JOIN daily_fx dfx ON hq.date = dfx.date AND s.Currencies_Id = dfx.Currencies_Id
    GROUP BY hq.date, a.Accounts_Name
    HAVING SUM(hq.qty_at_date) > 0  -- Εμφάνιση μόνο λογαριασμών με υπόλοιπο
    ORDER BY hq.date ASC, a.Accounts_Name ASC
*/
    -- Τελικός υπολογισμός ανά λογαριασμό ΚΑΙ Σύνολο (Total)
    SELECT 
        hq.date,
        COALESCE(a.Accounts_Name, 'Total') as Accounts_Name, -- Αν το όνομα είναι NULL (λόγω ROLLUP), βάλε 'Total'
        SUM(hq.qty_at_date * COALESCE(dp.price_close, 0) * 
            CASE WHEN cur_s.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(dfx.fx_rate, 1) END
        ) as account_value
    FROM historical_qty hq
    JOIN Accounts a ON hq.Accounts_Id = a.Accounts_Id
    JOIN Securities s ON hq.Securities_Id = s.Securities_Id
    JOIN Currencies cur_s ON s.Currencies_Id = cur_s.Currencies_Id
    LEFT JOIN daily_prices dp ON hq.date = dp.date AND hq.Securities_Id = dp.Securities_Id
    LEFT JOIN daily_fx dfx ON hq.date = dfx.date AND s.Currencies_Id = dfx.Currencies_Id
    GROUP BY hq.date, ROLLUP(a.Accounts_Name) -- <--- Προσθήκη ROLLUP
    HAVING SUM(hq.qty_at_date) > 0 
    ORDER BY hq.date ASC, (a.Accounts_Name IS NULL) ASC, a.Accounts_Name ASC



    """


    df = pd.read_sql(query_history_monthly, conn)
    conn.close()
    return df

@st.cache_data(ttl=3600)
def get_pnl_report_data():
    conn = get_connection()

    query_pnl = f"""
    WITH RECURSIVE 
    periods AS (
        SELECT 
            (date_trunc('day', CURRENT_DATE) - INTERVAL '1 day')::date as dtd_start,
            (date_trunc('week', CURRENT_DATE) - INTERVAL '1 day')::date as wtd_start,
            (date_trunc('month', CURRENT_DATE) - INTERVAL '1 day')::date as mtd_start,
            (date_trunc('year', CURRENT_DATE) - INTERVAL '1 day')::date as ytd_start,
            '1900-01-01'::date as all_time_start,
            CURRENT_DATE::date as today
    ),
    -- 1. Υπολογισμός Ποσοτήτων (Holdings) - Παραμένει ίδιο
    historical_holdings AS (
        SELECT 
            p.today, p.dtd_start, p.wtd_start, p.mtd_start, p.ytd_start, p.all_time_start,
            h.Accounts_Id, h.Securities_Id,
            h.Quantity as qty_today,
            h.Quantity - COALESCE((SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END) FROM Investment_Transactions WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id AND Date > p.dtd_start), 0) as qty_dtd,
            h.Quantity - COALESCE((SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END) FROM Investment_Transactions WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id AND Date > p.wtd_start), 0) as qty_wtd,
            h.Quantity - COALESCE((SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END) FROM Investment_Transactions WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id AND Date > p.mtd_start), 0) as qty_mtd,
            h.Quantity - COALESCE((SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END) FROM Investment_Transactions WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id AND Date > p.ytd_start), 0) as qty_ytd
        FROM periods p
        CROSS JOIN Holdings h
    ),
    -- 2. Λήψη Τιμών και FX Rates - Παραμένει ίδιο
    prices_fx AS (
        SELECT 
            hh.*,
            (SELECT Price_Close FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id AND Price_Date <= hh.today ORDER BY Price_Date DESC LIMIT 1) as price_today,
            (SELECT Price_Close FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id AND Price_Date <= hh.dtd_start ORDER BY Price_Date DESC LIMIT 1) as price_dtd,
            (SELECT Price_Close FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id AND Price_Date <= hh.wtd_start ORDER BY Price_Date DESC LIMIT 1) as price_wtd,
            (SELECT Price_Close FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id AND Price_Date <= hh.mtd_start ORDER BY Price_Date DESC LIMIT 1) as price_mtd,
            (SELECT Price_Close FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id AND Price_Date <= hh.ytd_start ORDER BY Price_Date DESC LIMIT 1) as price_ytd,
            (SELECT FX_Rate FROM Historical_FX WHERE Base_Currency_Id = s.Currencies_Id AND FX_Date <= hh.today ORDER BY FX_Date DESC LIMIT 1) as fx_today,
            (SELECT FX_Rate FROM Historical_FX WHERE Base_Currency_Id = s.Currencies_Id AND FX_Date <= hh.dtd_start ORDER BY FX_Date DESC LIMIT 1) as fx_dtd,
            (SELECT FX_Rate FROM Historical_FX WHERE Base_Currency_Id = s.Currencies_Id AND FX_Date <= hh.wtd_start ORDER BY FX_Date DESC LIMIT 1) as fx_wtd,
            (SELECT FX_Rate FROM Historical_FX WHERE Base_Currency_Id = s.Currencies_Id AND FX_Date <= hh.mtd_start ORDER BY FX_Date DESC LIMIT 1) as fx_mtd,
            (SELECT FX_Rate FROM Historical_FX WHERE Base_Currency_Id = s.Currencies_Id AND FX_Date <= hh.ytd_start ORDER BY FX_Date DESC LIMIT 1) as fx_ytd,
            s.Security_Name, a.Accounts_Name
        FROM historical_holdings hh
        JOIN Securities s ON hh.Securities_Id = s.Securities_Id
        JOIN Accounts a ON hh.Accounts_Id = a.Accounts_Id
    ),
    -- 3. ΒΕΛΤΙΩΜΕΝΟΣ Υπολογισμός Cash Flows (Περιλαμβάνει Dividends & Interest)
    cash_flows AS (
        SELECT 
            Accounts_Id, Securities_Id,
            -- DTD
            SUM(CASE WHEN Date > (SELECT dtd_start FROM periods) THEN 
                (CASE 
                    WHEN Action IN ('Buy', 'MiscExp') THEN Total_Amount 
                    WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -Total_Amount 
                    ELSE 0 END) 
                ELSE 0 END) as cf_dtd,
            -- WTD
            SUM(CASE WHEN Date > (SELECT wtd_start FROM periods) THEN 
                (CASE 
                    WHEN Action IN ('Buy', 'MiscExp') THEN Total_Amount 
                    WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -Total_Amount 
                    ELSE 0 END) 
                ELSE 0 END) as cf_wtd,
            -- MTD
            SUM(CASE WHEN Date > (SELECT mtd_start FROM periods) THEN 
                (CASE 
                    WHEN Action IN ('Buy', 'MiscExp') THEN Total_Amount 
                    WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -Total_Amount 
                    ELSE 0 END) 
                ELSE 0 END) as cf_mtd,
            -- YTD
            SUM(CASE WHEN Date > (SELECT ytd_start FROM periods) THEN 
                (CASE 
                    WHEN Action IN ('Buy', 'MiscExp') THEN Total_Amount 
                    WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -Total_Amount 
                    ELSE 0 END) 
                ELSE 0 END) as cf_ytd,
            -- All Time
            SUM(CASE 
                WHEN Action IN ('Buy', 'MiscExp') THEN Total_Amount 
                WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -Total_Amount 
                ELSE 0 END) as cf_all_time,
            -- All Time: Αγορές (εκροές) θετικές, Πωλήσεις/Μερίσματα (εισροές) αρνητικές
            SUM(CASE 
                WHEN Action IN ('Buy', 'CashIn', 'MiscExp') THEN Total_Amount 
                WHEN Action IN ('Sell', 'Dividend', 'IntInc', 'CashOut', 'RtrnCap') THEN -Total_Amount 
                ELSE 0 END) as net_invested_all_time                
        FROM Investment_Transactions
        GROUP BY Accounts_Id, Securities_Id
    )
    -- 4. Τελικό P&L Calculation
    SELECT 
        pf.Accounts_Name, pf.Security_Name,
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) as current_value_eur,
        -- P&L = Τρέχουσα Αξία - Αρχική Αξία - (Αγορές - Πωλήσεις - Μερίσματα)
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) - COALESCE(cf.cf_dtd, 0) as pnl_dtd_eur,
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_wtd * pf.price_wtd * COALESCE(pf.fx_wtd, 1)) - COALESCE(cf.cf_wtd, 0) as pnl_wtd_eur,
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_mtd * pf.price_mtd * COALESCE(pf.fx_mtd, 1)) - COALESCE(cf.cf_mtd, 0) as pnl_mtd_eur,
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)) - COALESCE(cf.cf_ytd, 0) as pnl_ytd_eur,
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE(cf.cf_all_time, 0) as pnl_all_time_eur,
        -- P&L All Time = Τρέχουσα Αξία - Καθαρό Επενδυμένο Κεφάλαιο (Net Invested)
        (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE(cf.net_invested_all_time, 0) as pnl_net_all_time_eur        
    FROM prices_fx pf
    LEFT JOIN cash_flows cf ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
    ORDER BY pf.Accounts_Name, pf.Security_Name;
    """

    df = pd.read_sql(query_pnl, conn)
    conn.close()
    return df

# Ορισμός συνάρτησης για το χρώμα
def color_change(val):
    color = 'red' if val < 0 else 'green' if val > 0 else 'blue'
    return f'color: {color}'


st.set_page_config(page_title="Finance OS", layout="wide")

# --- SIDEBAR ---
st.sidebar.title("💰 Finance OS")

menu = st.sidebar.radio(
    "Menu", 
    [
        "🏛️ Dashboard",       # Γενική εικόνα (σαν τράπεζα/κτίριο διοίκησης)
        "📝 Register",        # Καταγραφή κινήσεων (σαν λογιστικό βιβλίο)
        "🥧 Investments",     # Επενδύσεις (το Pie Chart δείχνει το Allocation)
        "⏳ Reports", # Ιστορικότητα (το ρολόι δείχνει την εξέλιξη στον χρόνο)
        "🌍 Market Data",     # Τιμές αγοράς/FX (η υδρόγειος για παγκόσμια δεδομένα)
        "🔧 Settings"         # Ρυθμίσεις (το κλειδί για παραμετροποίηση)
    ]
)

try:
    conn = get_connection()

    # --- 🏛 DASHBOARD (Ενοποιημένο με FX & Investments) ---
    if menu == "🏛️ Dashboard":
        st.title("🏛 Net Worth")
        t1, = st.tabs(["Current Net Worth"])


        with t1: # Current Net Worth
        
            query_combined = """
                WITH Latest_FX AS (
                    SELECT DISTINCT ON (Base_Currency_Id) Base_Currency_Id, FX_Rate 
                    FROM Historical_FX 
                    ORDER BY Base_Currency_Id, FX_Date DESC
                ),
                Latest_Prices AS (
                    SELECT DISTINCT ON (Securities_Id) Securities_Id, Price_Close 
                    FROM Historical_Prices 
                    ORDER BY Securities_Id, Price_Date DESC
                )

                -- ASSETS SECTION
                SELECT a.Accounts_Name as name, 'Assets' as type, c.Currencies_ShortName as curr, a.Account_Balance as qty,
                       CASE WHEN c.Currencies_ShortName = 'EUR' THEN a.Account_Balance ELSE a.Account_Balance * COALESCE(fx.FX_Rate, 1) END as value_eur
                FROM Accounts a 
                LEFT JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id 
                LEFT JOIN Latest_FX fx ON a.Currencies_Id = fx.Base_Currency_Id 
                WHERE a.Is_Active = TRUE AND a.Accounts_Type NOT IN ('Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Other')

               -- ('Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other') 
                UNION ALL

                -- CASH SECTION
                SELECT a.Accounts_Name as name, 'Cash' as type, c.Currencies_ShortName as curr, a.Account_Balance as qty,
                       CASE WHEN c.Currencies_ShortName = 'EUR' THEN a.Account_Balance ELSE a.Account_Balance * COALESCE(fx.FX_Rate, 1) END as value_eur
                FROM Accounts a 
                LEFT JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id 
                LEFT JOIN Latest_FX fx ON a.Currencies_Id = fx.Base_Currency_Id 
    --            WHERE a.Is_Active = TRUE AND a.Accounts_Type NOT IN ('Brokerage', 'Pension')
                WHERE a.Is_Active = TRUE AND a.Accounts_Type NOT IN ('Brokerage', 'Pension', 'Other Investment', 'Margin', 'Real Estate', 'Vehicle', 'Asset', 'Liability')

               -- ('Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other') 
                UNION ALL
                          
                -- PENSION SECTION
                SELECT a.Accounts_Name as name, 'Pension' as type, c.Currencies_ShortName as curr, a.Account_Balance as qty,
                       CASE WHEN c.Currencies_ShortName = 'EUR' THEN a.Account_Balance ELSE a.Account_Balance * COALESCE(fx.FX_Rate, 1) END as value_eur
                FROM Accounts a 
                LEFT JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id 
                LEFT JOIN Latest_FX fx ON a.Currencies_Id = fx.Base_Currency_Id 
                WHERE a.Is_Active = TRUE AND a.Accounts_Type IN ('Pension')

                UNION ALL
                
                -- INVESTMENT SECTION (Διορθωμένο με LEFT JOINS)
                SELECT 
                    COALESCE(s.Security_Name, 'Unknown Security') as name, 
                    'Investment' as type, 
                    COALESCE(c.Currencies_ShortName, 'EUR') as curr, 
                    h.Quantity as qty,
                    CASE 
                        WHEN COALESCE(c.Currencies_ShortName, 'EUR') = 'EUR' THEN h.Quantity * COALESCE(lp.Price_Close, 0) 
                        ELSE (h.Quantity * COALESCE(lp.Price_Close, 0)) * COALESCE(fx.FX_Rate, 1) 
                    END as value_eur
                FROM Holdings h 
                LEFT JOIN Securities s ON h.Securities_Id = s.Securities_Id 
                LEFT JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id 
                LEFT JOIN Latest_Prices lp ON s.Securities_Id = lp.Securities_Id 
                LEFT JOIN Latest_FX fx ON c.Currencies_Id = fx.Base_Currency_Id -- Join FX based on security currency
                WHERE h.Quantity <> 0
            """

            df_net = pd.read_sql(query_combined, conn)
            
            # ΠΟΛΥ ΣΗΜΑΝΤΙΚΟ: Μετατροπή όλων των στηλών σε πεζά
            df_net.columns = [c.lower() for c in df_net.columns]


            df_net['type'] = df_net['type'].str.strip()
            # 2. Συνάρτηση για το χρωματισμό των αρνητικών τιμών
            def color_negative_red(val):
                if val < 0:
                    color = 'red'
                elif val == 0:
                    color = 'blue'
                else:
                    # 'white' για dark theme, 'black' για light theme
                #    color = 'white' 
                    color = 'green' 
                
                return f'color: {color}'

            def style_qty_display(series_or_df):
                # Δημιουργούμε μια λίστα από κενά styles με το ίδιο μέγεθος
                styles = ['' for _ in range(len(series_or_df))]
                
                for i in range(len(series_or_df)):
                    val = series_or_df['qty'].iloc[i] # Κοιτάμε την αριθμητική τιμή της qty
                    
                    if val < 0:
                        styles[i] = 'color: red'
                    elif val == 0:
                        styles[i] = 'color: blue'
                    else:
                        styles[i] = 'color: green'
                        
                return styles

            
            # 1. Δημιουργία στήλης για εμφάνιση (Display Column)
            def format_qty(row):
    #            if row['type'] == 'Cash':
                if row['type'] in ['Cash', 'Assets', 'Pension']:
                    # Διαχωριστικό χιλιάδων με στάνταρ 2 δεκαδικά για μετρητά
                    val = f"{row['qty']:,.2f}"
                    symbols = {'EUR': '€', 'USD': '$', 'GBP': '£'}
                    sym = symbols.get(row['curr'], row['curr'])
                    return f"{sym} {val}"
                else:
                    # 1. Φορμάρουμε με έως 8 δεκαδικά και διαχωριστικό χιλιάδων
                    val = f"{row['qty']:,.8f}"
                    
                    # 2. Αφαιρούμε τα μηδενικά από το τέλος και την υποδιαστολή αν δεν χρειάζεται
                    if '.' in val:
                        val = val.rstrip('0').rstrip('.')
                        
                    # 3. Αν το αποτέλεσμα είναι "0" ή "-0" (λόγω floating point), το κάνουμε "0"
                    if val in ["0", "-0"]:
                        val = "0"
                        
                    return val

            df_net['qty_display'] = df_net.apply(format_qty, axis=1)


            # 3. Εφαρμογή Style και Formatting
            # Ορίζουμε τη νέα σειρά (qty_display πριν το value_eur)
            new_order = ['name', 'type', 'curr', 'qty', 'qty_display', 'value_eur']
            df_net = df_net.reindex(columns=new_order)
            # Χρησιμοποιούμε το .style για χρώμα και format ταυτόχρονα
            styled_df = df_net.style \
                .map(color_negative_red, subset=['value_eur', 'qty']) \
                .apply(lambda x: style_qty_display(df_net), subset=['qty_display'], axis=0) \
                .format({
                    "qty": "{:,.2f}",
                    "value_eur": "€ {:,.2f}"
                }) \
                .hide(['qty'], axis=1)  # Αν αυτό δεν δουλέψει, δοκίμασε: .hide(subset=['qty'], axis=1)


            # 2. Metrics (παραμένουν ως έχουν)
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Net Worth", f"€ {df_net['value_eur'].sum():,.2f}")
            m2.metric("Assets", f"€ {df_net[df_net['type']=='Assets']['value_eur'].sum():,.2f}")
            m3.metric("Cash", f"€ {df_net[df_net['type']=='Cash']['value_eur'].sum():,.2f}")
            m4.metric("Pension", f"€ {df_net[df_net['type']=='Pension']['value_eur'].sum():,.2f}")
            m5.metric("Investments", f"€ {df_net[df_net['type']=='Investment']['value_eur'].sum():,.2f}")
                    
            # 5. Εμφάνιση του Styled DataFrame
            # Ορίζεις τη σειρά που θέλεις, παραλείποντας την 'qty'
            st.dataframe(
                styled_df, 
                #use_container_width=True, 
                width="stretch", 
                hide_index=True,
                column_order=("name", "type", "curr", "qty_display", "value_eur"),
                column_config={
                    "name": "Περιγραφή",
                    "type": "Κατηγορία",
                    "curr": "Νόμισμα",
                    "qty_display": "Ποσότητα", # Εμφανίζεται ως String πλέον
                    "value_eur": "Αξία (€)"}
            )


            # --- UI elements για την Ενημέρωση ---
            st.subheader("🔄 Ενημέρωση Υπολοίπου Λογαριασμών (Bank & Cash Accounts)")

            # Το κουμπί που καλεί τη συνάρτηση
            if st.button("🚀 Ενημέρωση Λογαριασμών (Bank & Cash Accounts)"):
                with st.spinner("Παρακαλώ περιμένετε, η διαδικασία είναι σε εξέλιξη..."):
                    update_account_balances()
                    st.balloons() # Εφέ επιτυχίας

            # --- UI elements για την Ενημέρωση ---
            st.subheader("🔄 Ενημέρωση Υπολοίπου Λογαριασμών (Pension Accounts)")

            # Το κουμπί που καλεί τη συνάρτηση
            if st.button("🚀 Ενημέρωση Λογαριασμών (Pension Accounts)"):
                with st.spinner("Παρακαλώ περιμένετε, η διαδικασία είναι σε εξέλιξη..."):
                    update_pension_balances()
                    st.balloons() # Εφέ επιτυχίας





    # --- 📝 REGISTER (Fixed Case Sensitivity & Logic) ---
    elif menu == "📝 Register":
        st.title("📝 Account Transactions Register")
        
        # 1. Φόρτωση Λογαριασμών & Payees
        df_accs = pd.read_sql("SELECT * FROM Accounts WHERE Is_Active = True", conn)
        df_payees = pd.read_sql("SELECT Payees_Id, Payees_Name FROM Payees", conn)
        
        # Βοηθητικά δεδομένα για τα Dropdowns (τα φορτώνουμε μία φορά)
    #    df_acc_list = pd.read_sql("SELECT Accounts_Id, Accounts_Name FROM Accounts", conn)
        df_payee_list = pd.read_sql("SELECT Payees_Id, Payees_Name FROM Payees", conn)

        # Σωστός ορισμός
        #acc_options = df_acc_list.set_index('accounts_id')['accounts_name'].to_dict()

        # 1. Δημιουργία του χάρτη λογαριασμών (όπως τον είχες)
        acc_options = {
            row['accounts_id']: f"{row['accounts_name']} ({row['account_balance']:,.2f})" 
            for _, row in df_accs.iterrows()
        }
        acc_ids_list = list(acc_options.keys())


        payee_options = df_payee_list.set_index('payees_id')['payees_name'].to_dict()

        # Μετατρέπουμε το dataframe σε λίστα από λεξικά (records) ρητά
        account_list = df_accs.to_dict('records')

        # 1. Φόρτωση Ιεραρχίας Κατηγοριών (Full Path)
        query_cat_hierarchy = """
        WITH RECURSIVE CategoryHierarchy AS (
            SELECT Categories_Id, Categories_Name::TEXT as Full_Path
            FROM Categories 
            WHERE Parent_Category_Id IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name
            FROM Categories c
            JOIN CategoryHierarchy ch ON c.Parent_Category_Id = ch.Categories_Id
        )
        SELECT Categories_Id, Full_Path FROM CategoryHierarchy ORDER BY Full_Path;
        """
        df_cat_list = pd.read_sql(query_cat_hierarchy, conn)
        # Δημιουργία του dictionary που έλειπε
        cat_options = df_cat_list.set_index('categories_id')['full_path'].to_dict()
       
        # 1. Δημιουργούμε ένα dictionary για το mapping {id: "Name (Balance)"}
        # Χρησιμοποιούμε list comprehension για να φτιάξουμε τις επιλογές
    #    acc_map = {
    #        row['accounts_id']: f"{row['accounts_name']} ({row['account_balance']:,.2f})" 
    #        for _, row in df_accs.iterrows()
    #    }
        
        # 2. Το selectbox τώρα δουλεύει με απλά IDs (Integers), όχι με αντικείμενα
    #    acc_id = st.selectbox(
    #        "Select Account:", 
    #        options=list(acc_map.keys()), # Λίστα από IDs
    #        format_func=lambda x: acc_map[x] # Εμφάνιση του ονόματος από το dictionary
    #    )

        # 2. Αρχικοποίηση αν δεν υπάρχει (για να μην πετάει το AttributeError)
        if "account_id_internal" not in st.session_state:
            st.session_state["account_id_internal"] = acc_ids_list[0] if acc_ids_list else None


        # 3. Το Selectbox με χρήση του key
        # Το Streamlit θα αποθηκεύει αυτόματα την επιλογή στο st.session_state["account_id_internal"]
        acc_id = st.selectbox(
            "Select Account:", 
            options=acc_ids_list,
            format_func=lambda x: acc_options.get(x, "Unknown"),
            key="account_id_internal" 
        )


        # Ενημέρωση του index στο state όταν αλλάζει ο χρήστης χειροκίνητα
        st.session_state.selected_acc_index = acc_ids_list.index(acc_id)





        # 3. Βρίσκουμε τον τύπο του λογαριασμού από το dataframe βάσει του επιλεγμένου id
        acc_type = df_accs.loc[df_accs['accounts_id'] == acc_id, 'accounts_type'].values[0]

        if acc_type not in ['Brokerage', 'Pension']:
            # Tabbed interface για καθαρότητα
            t_view, t_new = st.tabs(["👁️ View Register", "➕ New Transaction / Transfer"])

            if "selected_acc_index" not in st.session_state:
                st.session_state.selected_acc_index = 0  # Προεπιλογή ο πρώτος λογαριασμός

            with t_new:
                st.info("Ορίστε τη συναλλαγή και από κάτω την ανάλυση σε κατηγορίες (Splits)")
                
                # Φόρμα κύριας συναλλαγής
                with st.form("tx_form_with_splits"):
                    c1, c2 = st.columns(2)
                    date = c1.date_input("Ημερομηνία", datetime.now())
                    payee = c2.selectbox("Payee", df_payees.to_dict('records'), format_func=lambda x: x['payees_name'])
                    total_amount = st.number_input("Συνολικό Ποσό", value=0.0)
                    desc = st.text_input("Περιγραφή")
                    
                    # Προετοιμασία κενού DataFrame για τα νέα Splits
                    df_new_splits = pd.DataFrame(columns=['categories_id', 'amount', 'memo'])
                    
                    st.write("---")
                    st.write("📂 Ανάλυση Κατηγοριών (Splits)")
                    # Editor για τα νέα splits
                    new_splits_data = st.data_editor(
                        df_new_splits, 
                        num_rows="dynamic", 
                        key="new_splits_editor",
                        column_config={
                            "categories_id": st.column_config.SelectboxColumn("Category", options=list(cat_options.keys()), format_func=lambda x: cat_options.get(x, "Unknown"))
                        }
                    )

                    if st.form_submit_button("🔥 Καταχώρηση Συναλλαγής & Splits"):
                        # 1. Διασφάλιση ότι τα ποσά είναι αριθμοί (float)
                        try:
                            # Μετατροπή του total_amount σε float
                            val_total_amount = float(total_amount)
                            
                            # Μετατροπή της στήλης amount σε αριθμητική και άθροισμα (τα κενά γίνονται 0)
                            splits_total = pd.to_numeric(new_splits_data['amount'], errors='coerce').fillna(0).sum()
                            
                            # 2. Έλεγχος αν το άθροισμα ταιριάζει
                            if abs(float(splits_total) - val_total_amount) > 0.01:
                                st.error(f"Το άθροισμα των Splits ({splits_total:,.2f}) δεν ισούται με το Συνολικό Ποσό ({val_total_amount:,.2f})")
                            else:
                                cur = conn.cursor()
                                # ... συνεχίστε με το INSERT ...
                                cur.execute("""
                                    INSERT INTO Bank_Transactions (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared)
                                    VALUES (%s, %s, %s, %s, %s, True) RETURNING Bank_Transactions_Id
                                """, (acc_id, date, payee['payees_id'], desc, val_total_amount))
                                
                                new_id = cur.fetchone()[0] # Προσθήκη [0] για να πάρουμε την τιμή από το tuple
                                
                                # 3. Εισαγωγή των Splits με μετατροπή σε float
                                for _, row in new_splits_data.iterrows():
                                    row_amount = float(pd.to_numeric(row['amount'], errors='coerce') or 0)
                                    cur.execute("""
                                        INSERT INTO Bank_Transaction_Splits (Bank_Transactions_Id, Categories_Id, Amount, Memo)
                                        VALUES (%s, %s, %s, %s)
                                    """, (new_id, row['categories_id'], row_amount, row['memo']))
                                
                                conn.commit()
                                update_account_balances(st.session_state["account_id_internal"]) # Ενημέρωση
                                st.success("Η συναλλαγή και τα splits αποθηκεύτηκαν!")
                                st.rerun()
                                
                        except ValueError:
                            st.error("Παρακαλώ εισάγετε έγκυρα αριθμητικά ποσά.")



            with t_view:
                query_reg = f"SELECT * FROM Bank_Transactions WHERE Accounts_Id = {acc_id} OR Target_Account_Id = {acc_id} ORDER BY Date DESC"
                df = pd.read_sql(query_reg, conn)
                
             
                # 1. Ο Editor παραμένει ως έχει για προβολή/επεξεργασία
                # 1. Ο Editor με σωστό column_config (Dictionary αντί για Set)
                unique_key = f"set_reg_{acc_id}"
                edited_reg = st.data_editor(
                    df, 
                    num_rows="dynamic", 
                    key=unique_key, 
                    width="stretch", 
                    column_config={
                        "accounts_id": st.column_config.SelectboxColumn("Account", options=list(acc_options.keys()), format_func=lambda x: acc_options.get(x, "Unknown")),
                        "payees_id": st.column_config.SelectboxColumn("Payee", options=list(payee_options.keys()), format_func=lambda x: payee_options.get(x, "Unknown")),
                        "target_account_id": st.column_config.SelectboxColumn("Target Account", options=list(acc_options.keys()), format_func=lambda x: acc_options.get(x, "Unknown"))
                    }
                )



                #st.write(df.columns.tolist())





               # --- 1. Κεντρικός Πίνακας Συναλλαγών ---
                if not edited_reg.equals(df):
                    # Αποθήκευση χωρίς να μπλοκάρουμε τον χρήστη
                    save_changes(df, edited_reg, "Bank_Transactions", "bank_transactions_id", current_acc_id=acc_id)
                    st.rerun()

                # --- 2. Υπολογισμός Ασυμφωνίας (για εμφάνιση Warning) ---
                # Παίρνουμε το ποσό της επιλεγμένης συναλλαγής
                if st.session_state.current_tx_id:
                    main_tx = df[df['bank_transactions_id'] == st.session_state.current_tx_id]
                    if not main_tx.empty:
                        actual_total = float(main_tx.iloc[0]['total_amount'])
                        
                        # Υπολογισμός αθροίσματος splits από τη βάση
                        sum_query = "SELECT SUM(amount) as s FROM Bank_Transaction_Splits WHERE Bank_Transactions_Id = %s"
                        db_splits_sum = float(pd.read_sql(sum_query, conn, params=(int(st.session_state.current_tx_id),)).iloc[0]['s'] or 0)
                        
                        if abs(actual_total - db_splits_sum) > 0.01:
                            st.warning(f"⚠️ **Ασυμφωνία:** Η συναλλαγή είναι {actual_total:,.2f} αλλά τα splits είναι {db_splits_sum:,.2f}. Παρακαλώ διορθώστε τα splits παρακάτω.")


                st.write("---")

                # 2. Χειροκίνητη επιλογή για τα Splits
                st.subheader("🔍 Ανάλυση Splits")
                col_sel, col_btn = st.columns([2, 1])

                # 1. Αρχικοποίηση της κατάστασης εμφάνισης στο session_state
                if "show_splits_pane" not in st.session_state:
                    st.session_state.show_splits_pane = False
                if "current_tx_id" not in st.session_state:
                    st.session_state.current_tx_id = None

                col_sel, col_btn = st.columns([2, 1])

                with col_sel:
                    available_ids = df['bank_transactions_id'].tolist()
                    # Χρησιμοποιούμε index για να διατηρείται η επιλογή στο rerun
                    default_ix = 0
                    if st.session_state.current_tx_id in available_ids:
                        default_ix = available_ids.index(st.session_state.current_tx_id) + 1
                        
                    selected_tx_id = st.selectbox("Επιλέξτε ID Συναλλαγής για Splits:", [None] + available_ids, index=default_ix)

                #with col_btn:
                #    st.write(" ") 
                #    if st.button("Προβολή Splits"):
                #        st.session_state.show_splits_pane = True
                #        st.session_state.current_tx_id = selected_tx_id
                #        st.rerun()


                with col_btn:
                    st.write(" ") 
                    # Προσθέτουμε και το acc_id στο key για απόλυτη μοναδικότητα
                    btn_key = f"view_splits_{acc_id}_{selected_tx_id}"
                    if st.button("Προβολή Splits", key=btn_key):
                        st.session_state.show_splits_pane = True
                        st.session_state.current_tx_id = selected_tx_id
                        st.rerun()



                # 2. Έλεγχος εμφάνισης από το session_state (όχι από το κουμπί απευθείας)
                if st.session_state.show_splits_pane and st.session_state.current_tx_id:
                    # Αν ο χρήστης άλλαξε ID στο selectbox, κλείσε το pane ή ενημέρωσέ το
                    if selected_tx_id != st.session_state.current_tx_id:
                        st.session_state.show_splits_pane = False
                        st.session_state.current_tx_id = None
                        st.rerun()

                    st.write("---")
                    st.write(f"### 📑 Edit Splits for ID: {st.session_state.current_tx_id}")
                    
                    # Φόρτωση δεδομένων
                    df_splits = pd.read_sql("SELECT * FROM Bank_Transaction_Splits WHERE Bank_Transactions_Id = %s", 
                                            conn, params=(int(st.session_state.current_tx_id),))
                    
                    edited_splits = st.data_editor(
                        df_splits,
                        num_rows="dynamic",
                        key=f"splits_ed_{st.session_state.current_tx_id}",
                        width="stretch",
                        column_config={
                            "categories_id": st.column_config.SelectboxColumn(
                                "Category", 
                                options=list(cat_options.keys()), 
                                format_func=lambda x: cat_options.get(x, "Unknown"),
                                width="large"
                            ),
                            "bank_transactions_id": None
                        }
                    )
                    
                    #if st.button("💾 Save Splits Changes"):
                    #    edited_splits['bank_transactions_id'] = st.session_state.current_tx_id
                    #    commit_changes(df_splits, edited_splits, "Bank_Transaction_Splits", "split_id")
                    # Πρόσθεσε ένα μοναδικό key χρησιμοποιώντας το current_tx_id


                    # Χρήση f-string για εγγυημένη μοναδικότητα
            #        button_key = f"save_splits_db_{st.session_state.current_tx_id}"

                    # Το key περιλαμβάνει acc_id και tx_id
                    save_btn_key = f"save_splits_{acc_id}_{st.session_state.current_tx_id}"
                    
                    if st.button("💾 Save Splits Changes", key=f"save_{st.session_state.current_tx_id}"):
                        # Εδώ αποθηκεύουμε ΠΑΝΤΑ, αλλά ενημερώνουμε τον χρήστη αν ακόμα δεν ταιριάζουν
                        new_splits_sum = edited_splits['amount'].sum()
                        
                        # Update στη βάση
                        edited_splits['bank_transactions_id'] = st.session_state.current_tx_id
                        commit_changes(df_splits, edited_splits, "Bank_Transaction_Splits", "split_id")
                        
                        if abs(new_splits_sum - actual_total) > 0.01:
                            st.info(f"Τα splits αποθηκεύτηκαν, αλλά υπολείπονται {actual_total - new_splits_sum:,.2f} για να συμφωνούν με τη συναλλαγή.")
                        else:
                            st.success("✅ Όλα συμφωνούν!")
                        st.rerun()

        else: # Investment Register
            df_inv = pd.read_sql(f"SELECT * FROM Investment_Transactions WHERE Accounts_Id = {acc_id} ORDER BY Date DESC", conn)
            #save_changes(st.data_editor(df_inv, use_container_width=True, key="inv_reg"), "Investment_Transactions", "inv_transactions_id")
            save_changes(df_inv, st.data_editor(df_inv, width="stretch", key="inv_reg"), "Investment_Transactions", "inv_transactions_id")



    # --- 🥧 ΕΠΕΝΔΥΣΕΙΣ ---
    elif menu == "🥧 Investments":
        st.title("🥧 Investment Portfolio & Transactions")
        
        conn = get_connection()
        
        # 1. Επιλογή Επενδυτικού Λογαριασμού (Dropdown)
    #    df_inv_accs = pd.read_sql("SELECT Accounts_Id, Accounts_Name FROM Accounts WHERE Accounts_Type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')", conn)
        df_inv_accs = pd.read_sql("SELECT Accounts_Id, Accounts_Name, (SELECT SUM(CASE WHEN Action IN ('Buy', 'Reinvest', 'ShrIn') THEN Quantity WHEN Action IN ('Sell', 'ShrOut') THEN -Quantity ELSE 0 END) FROM Investment_Transactions WHERE Investment_Transactions.Accounts_Id = Accounts.Accounts_Id) Account_Position, (SELECT SUM(CASE WHEN Action IN ('Dividend', 'CashIn', 'IntInc') THEN Total_Amount WHEN Action IN ( 'CashOut') THEN -Total_Amount ELSE 0 END) FROM Investment_Transactions WHERE Investment_Transactions.Accounts_Id = Accounts.Accounts_Id) Account_Amount FROM Accounts WHERE Accounts_Type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')", conn)

        if df_inv_accs.empty:
            st.warning("⚠️ Δεν βρέθηκαν επενδυτικοί λογαριασμοί. Ορίστε έναν λογαριασμό ως 'Brokerage' ή 'Pension' στις Ρυθμίσεις.")
        else:
            selected_inv_acc = st.selectbox(
                "Select Investment / Pension Account:", 
                df_inv_accs.to_dict('records'), 
#                format_func=lambda x: x['accounts_name']
                 format_func=lambda x: f"{x['accounts_name']} ({x['account_position']:,.8f}) ({x['account_amount']:,.2f})"
            )
            inv_acc_id = selected_inv_acc['accounts_id']

            # 2. Φόρτωση βοηθητικών δεδομένων για Securities (για να βλέπουμε ονόματα στο Register)
            df_sec_list = pd.read_sql("SELECT Securities_Id, Security_Name FROM Securities", conn)
            sec_options = df_sec_list.set_index('securities_id')['security_name'].to_dict()

            # --- TABS ΓΙΑ ΔΙΑΧΩΡΙΣΜΟ REGISTER & HOLDINGS ---
            tab_reg, tab_hold = st.tabs(["📓 Investment Register", "📊 Current Holdings"])

            with tab_reg:
                st.subheader(f"Transaction History: {selected_inv_acc['accounts_name']}")
                
                # Φόρτωση συναλλαγών μόνο για τον επιλεγμένο λογαριασμό
                df_inv_tx = pd.read_sql(f"SELECT * FROM Investment_Transactions WHERE Accounts_Id = {inv_acc_id} ORDER BY Date DESC", conn)
                
                edited_inv_tx = st.data_editor(
                    df_inv_tx,
                    num_rows="dynamic",
                    key=f"inv_tx_editor_{inv_acc_id}",
                    #use_container_width=True,
                    width="stretch",
                    column_config={
                        "inv_transactions_id": st.column_config.NumberColumn("ID", disabled=True),
                        "securities_id": st.column_config.SelectboxColumn(
                            "Security",
                            options=list(sec_options.keys()),
                            format_func=lambda x: sec_options.get(x, "Unknown"),
                            required=True
                        ),
                        "action": st.column_config.SelectboxColumn(
                            "Action", 
                            options=['Buy', 'Sell', 'Dividend', 'Reinvest', 'Split', 'ShrIn', 'ShrOut', 'IntInc', 'CashIn', 'CashOut', 'Vest', 'Expire', 'Grant', 'Exercise', 'MiscExp', 'RtrnCap'],
                            required=True
                        ),
                        "quantity": st.column_config.NumberColumn("Shares", format="%.8f"),
                        "price_per_share": st.column_config.NumberColumn("Price", format="%.4f"),
                        "total_amount": st.column_config.NumberColumn("Total Cash", format="%.2f")
                    }
                )
                save_changes(df_inv_tx, edited_inv_tx, "Investment_Transactions", "inv_transactions_id")

            with tab_hold:
                st.subheader(f"Current Holdings: {selected_inv_acc['accounts_name']}")
                df_h = pd.read_sql(f"SELECT * FROM Holdings WHERE Accounts_Id = {inv_acc_id}", conn)
                edited_h = st.data_editor(
                    df_h, 
                    key=f"inv_h_editor_{inv_acc_id}",
                    #use_container_width=True,
                    width="stretch",
                    column_config={
                        "securities_id": st.column_config.SelectboxColumn(
                            "Security",
                            options=list(sec_options.keys()),
                            format_func=lambda x: sec_options.get(x, "Unknown")
                        )
                    }
                )
                save_changes(df_h, edited_h, "Holdings", "holdings_id")

                        # --- UI elements για την Ενημέρωση ---
                st.subheader("🔄 Update Holdings")

                # Το κουμπί που καλεί τη συνάρτηση
                if st.button("🚀 Update Holdings"):
                    with st.spinner("Please wait, the process is in progress..."):
                        update_holdings()
                        st.balloons() # Εφέ επιτυχίας

        conn.close()



    # --- ⏳️ Reports ---
    elif menu == "⏳ Reports":
        st.title("Reports")
        
        # Υπομενού στο sidebar για να μην τρέχουν όλα μαζί
        hist_sub_menu = st.sidebar.radio(
            "Επιλέξτε Αναφορά:",
            ["Historical Net Worth", "Historical Investment Positions", "P&L Reports", "Incomes & Expenses"],
            key="hist_sub_nav"
        )

                
        #tab_net_worth_hist, tab_inv_hist, tab_pnl = st.tabs(["Historical Net Worth", "Historical Investment Positions", "P&L Reports"])
        
        # 1. Αρχικοποίηση στο session_state (βάλτε το αυτό στην αρχή της "Historical Data")
        if "nw_date_val" not in st.session_state:
            st.session_state.nw_date_val = pd.Timestamp(dt_lib.date.today().year - 1, 12, 31)

        if "inv_date_val" not in st.session_state:
            st.session_state.inv_date_val = pd.Timestamp(dt_lib.date.today().year - 1, 12, 31)
        
        
        
        # HISTORICAL NET WORTH
        if hist_sub_menu == "Historical Net Worth":        
        #with tab_net_worth_hist: # Historical Net Worth
            #st.write("---")
            st.subheader("📈 Net Worth Progress (Monthly)")
            
            # Υπολογισμός τελευταίας ημέρας προηγούμενου μήνα
            last_day_prev_month = pd.Timestamp.now().replace(day=1) - pd.Timedelta(days=1)

            min_nwt_date = st.sidebar.date_input(
                "📅 Ημερομηνία Έναρξης Ιστορικού", 
                value=st.session_state.nw_date_val, # <--- Παίρνει την τιμή από το state
                max_value=last_day_prev_month, # <--- Περιορισμός στον προηγούμενο μήνα
                key="nw_date"
            )

            # Αποθήκευση της νέας επιλογής στο state για να τη θυμάται
            st.session_state.nw_date_val = min_nwt_date
    
            # Κλήση της cached συνάρτησης
            df_hist = get_hist_net_worth_data(min_nwt_date)

            if st.sidebar.button("🔄 Refresh Net Worth"):
                get_hist_net_worth_data.clear()  # Διαγράφει την cache ΜΟΝΟ για τη συγκεκριμένη συνάρτηση
                st.cache_data.clear() # Καθαρίζει τα πάντα για σιγουριά
                st.rerun() # Επαναφέρει την εφαρμογή για να τρέξει το query αμέσως
                
            try:
                #df_hist = pd.read_sql(query_history_monthly, conn)
                df_hist.columns = [c.lower() for c in df_hist.columns]
                df_hist['date'] = pd.to_datetime(df_hist['date'])
                df_hist = df_hist.sort_values('date')

                tab1, tab2 = st.tabs(["📊 Graph", "📋 Data"])
                
                with tab1:

                    # 1. Υπολογισμός μεταβολών για την ανίχνευση κορυφών και πτώσεων
                    df_hist['net_change'] = df_hist['total_net_worth'].diff()
                    max_gain_idx = df_hist['net_change'].idxmax()
                    max_loss_idx = df_hist['net_change'].idxmin()

                    # 2. Δημιουργία βασικού γραφήματος
                    fig = px.line(
                        df_hist, 
                        x="date", 
                        y=["total_cash", "total_invested", "total_pension", "total_assets"],
                        color_discrete_sequence=["#FFD700", "#457B9D", "#A8DADC", "#5D6D7E"],
                        template="plotly_dark"
                    )

                    # 3. Ρύθμιση λεπτότερων γραμμών για τις κατηγορίες
                    fig.update_traces(line=dict(width=2))

                    # 4. Προσθήκη της ΠΑΧΙΑΣ γραμμής Total Net Worth
                    fig.add_trace(
                        go.Scatter(
                            x=df_hist["date"], 
                            y=df_hist["total_net_worth"],
                            name="<b>TOTAL NET WORTH</b>",
                            line=dict(color="white", width=5),
                            hovertemplate="<b>%{y:,.0f} €</b>"
                        )
                    )

                    # 5. Προσθήκη Βελών (Annotations) για Max Gain & Max Loss
                    # Βέλος για τη μεγαλύτερη άνοδο
                    fig.add_annotation(
                        x=df_hist.loc[max_gain_idx, 'date'],
                        y=df_hist.loc[max_gain_idx, 'total_net_worth'],
                        text="🚀 Max Gain",
                        showarrow=True,
                        arrowhead=2,
                        arrowcolor="#2ECC71",
                        ax=0, ay=-40,
                        font=dict(color="#2ECC71", size=12)
                    )

                    # Βέλος για τη μεγαλύτερη πτώση
                    fig.add_annotation(
                        x=df_hist.loc[max_loss_idx, 'date'],
                        y=df_hist.loc[max_loss_idx, 'total_net_worth'],
                        text="🔻 Max Loss",
                        showarrow=True,
                        arrowhead=2,
                        arrowcolor="#E74C3C",
                        ax=0, ay=40,
                        font=dict(color="#E74C3C", size=12)
                    )

                    # 6. Layout

                    # Υπολογισμός του min και max date για τον άξονα
                    min_date = df_hist['date'].min()
                    max_date = df_hist['date'].max()

                    fig.update_layout(
                        yaxis_tickformat=',.0f', 
                        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
                        title="<b>Ανάλυση Περιουσίας & Μεταβολών</b>",
                        # ... οι υπόλοιπες ρυθμίσεις σας ...
                        xaxis=dict(
                            range=[min_date, max_date], # Επιβολή ολόκληρου του εύρους
                            type='date',
                            tickformat="%b %Y",         # Εμφάνιση Μήνα και Έτους
                            dtick="M12"                 # Εμφάνιση tick ανά 12 μήνες (ετήσια) για καθαρότητα
                        ),
                        hovermode="x unified",
                        # ...
                        margin=dict(l=0, r=0, t=100, b=0)
                    )

                    #st.plotly_chart(fig, use_container_width=True)
                    st.plotly_chart(fig, width="stretch")
                      
                with tab2:
                    st.dataframe(
                        df_hist.sort_values('date', ascending=False)
                        .style
                        # 2. Εφαρμογή του χρώματος ΜΟΝΟ στη στήλη net_change
                        .map(color_change, subset=['net_change'])
                        # 3. Μορφοποίηση των αριθμών (όπως την είχατε)
                        .format({
                            "total_assets": "€ {:,.2f}",
                            "total_cash": "€ {:,.2f}",
                            "total_pension": "€ {:,.2f}",
                            "total_invested": "€ {:,.2f}",
                            "total_net_worth": "€ {:,.2f}",
                            "net_change": "€ {:,.2f}"
                        }),
                        #use_container_width=True,
                        width="stretch",
                        hide_index=True
                    )                  
                  
            except Exception as e:
                st.error(f"Σφάλμα: {e}")

        
        elif hist_sub_menu == "Historical Investment Positions":        
        #with tab_inv_hist:
            # HISTORICAL INVESTMENT POSITIONS
            #st.write("---")
            st.subheader("📈 Investments Position Progress (Monthly)")
            
            # Υπολογισμός τελευταίας ημέρας προηγούμενου μήνα
            last_day_prev_month = pd.Timestamp.now().replace(day=1) - pd.Timedelta(days=1)

            min_inv_date = st.sidebar.date_input(
                "📅 Ημερομηνία Έναρξης Ιστορικού", 
                value=st.session_state.inv_date_val, # <--- Παίρνει τη δική του τιμή
                max_value=last_day_prev_month, # <--- Περιορισμός στον προηγούμενο μήνα
                key="inv_date"
            )

            # Αποθήκευση της νέας επιλογής στο state
            st.session_state.inv_date_val = min_inv_date
                    
            # Κλήση της cached συνάρτησης
            df_inv = get_hist_inv_positions_data(min_inv_date)

            if st.sidebar.button("🔄 Refresh Positions"):
                get_hist_inv_positions_data.clear()  # Διαγράφει την cache ΜΟΝΟ για τη συγκεκριμένη συνάρτηση
                st.cache_data.clear() # Καθαρίζει τα πάντα για σιγουριά
                st.rerun() # Επαναφέρει την εφαρμογή για να τρέξει το query αμέσως


            try:
                #df_hist = pd.read_sql(query_history_monthly, conn)
                
                # 1. Κανονικοποίηση στηλών (πεζά)
                df_inv.columns = [c.lower() for c in df_inv.columns]

                # --- ΚΡΙΣΙΜΗ ΠΡΟΣΘΗΚΗ: Μετατροπή σε datetime ---
                df_inv['date'] = pd.to_datetime(df_inv['date'])
             
                # 2. Pivot για το γράφημα και τον πίνακα
                df_pivot = df_inv.pivot(
                    index='date', 
                    columns='accounts_name', 
                    values='account_value'
                ).fillna(0).reset_index()

                # Δημιουργία των Tabs
                tab_graph, tab_data = st.tabs(["📊 Graph", "📋 Data Table"])

                with tab_graph:
                    # --- Plotly Line Chart ---
                    fig = px.line(
                        df_pivot, 
                        x="date", 
                        y=[c for c in df_pivot.columns if c != 'date'],
                        title="<b>Investment Value per Account</b>",
                        labels={"value": "Αξία (€)", "date": "Ημερομηνία", "variable": "Λογαριασμός"},
                        template="plotly_dark"
                    )
                    
                    # --- Προσαρμογή εμφάνισης γραμμής TOTAL ---
                    fig.for_each_trace(lambda t: t.update(
                        line=dict(color="white", width=4) if t.name.upper() == "TOTAL" else dict(width=2)
                    ))

                    # Προαιρετικά: Φέρνει τη γραμμή TOTAL "πάνω" από τις άλλες για να μην καλύπτεται
                    fig.update_layout(legend_traceorder="normal")
                    
                    fig.update_layout(
                        hovermode="x unified",
                        yaxis_tickformat=',.0f',
                        xaxis=dict(range=[df_pivot['date'].min(), df_pivot['date'].max()], type='date')
                    )
                    #st.plotly_chart(fig, use_container_width=True)
                    st.plotly_chart(fig, width="stretch")

                with tab_data:
                    st.subheader("Αναλυτικά Δεδομένα Επενδύσεων")
                    
                    # 1. Προετοιμασία και ταξινόμηση
                    df_display = df_pivot.copy().sort_values('date', ascending=False)
                    numeric_cols = [c for c in df_display.columns if c != 'date']
                    
                    # 2. Μορφοποίηση ημερομηνίας
                    df_display['date'] = df_display['date'].dt.strftime('%Y-%m-%d')

                    # 3. ΟΡΙΣΤΙΚΗ ΛΥΣΗ: Χρήση NumberColumn με format που περιλαμβάνει το κόμμα
                    # Το Streamlit στο NumberColumn στοιχίζει ΠΑΝΤΑ δεξιά.
                    # Το κλειδί είναι το format="%,.2f €" (το κόμμα πριν την τελεία)
                    col_config = {
                        col: st.column_config.NumberColumn(
                            col,
                            format="%,.2f €", # Εδώ επιβάλλουμε το κόμμα ΚΑΙ το ευρώ
                            width="medium",
                        ) for col in numeric_cols
                    }

                    # 4. Εμφάνιση - Στέλνουμε τα ΚΑΘΑΡΑ νούμερα (df_display χωρίς apply)
                    # ώστε το NumberColumn να κάνει τη δουλειά του σωστά
                    st.dataframe(
                        df_display, 
                        #use_container_width=True,
                        width="stretch",
                        hide_index=True,
                        column_config=col_config
                    )

            except Exception as e:
                st.error(f"Σφάλμα: {e}")



        # P&L REPORTS
        elif hist_sub_menu == "P&L Reports":
            #st.write("---")
            tab_report, tab_movers = st.tabs(["📊 P&L Report", "🚀 Top Movers"])

            with tab_report:
                #st.write("---")
                st.subheader("📈 Investments Profit & Loss")
            
                # Κλήση της cached συνάρτησης
                df_pnl = get_pnl_report_data()
                
                if st.sidebar.button("🔄 Refresh P&L"):
                    get_pnl_report_data.clear()  # Διαγράφει την cache ΜΟΝΟ για τη συγκεκριμένη συνάρτηση
                    with st.spinner("Running :green[download_historical_fx('1d')]"):
                        download_historical_fx("1d")
                    with st.spinner("Running :green[download_historical_prices_from_yahoo('1d')]"):
                        download_historical_prices_from_yahoo("1d")
                    st.cache_data.clear() # Καθαρίζει τα πάντα για σιγουριά
                    st.rerun() # Επαναφέρει την εφαρμογή για να τρέξει το query αμέσως

                try:
                    
                    # 1. Συνολικά Metrics
                    # Δημιουργία δύο στηλών για τα metrics
                    col1, col2, col3, col4, col5 = st.columns(5)

                    with col1:
                        total_dtd_pnl = df_pnl['pnl_dtd_eur'].sum()
                        total_current_value = df_pnl['current_value_eur'].sum()
                        st.metric("Total Current Value (EUR)", f"{total_current_value:,.2f} €", delta=f"{total_dtd_pnl:,.2f} €")
                    with col2:
                        total_wtd_pnl = df_pnl['pnl_wtd_eur'].sum()
                        st.metric("Total WTD P&L", f"{total_wtd_pnl:,.2f} €", delta=f"{total_wtd_pnl:,.2f} €")
                    with col3:
                        total_mtd_pnl = df_pnl['pnl_mtd_eur'].sum()
                        st.metric("Total MTD P&L", f"{total_mtd_pnl:,.2f} €", delta=f"{total_mtd_pnl:,.2f} €")
                    with col4:
                        total_ytd_pnl = df_pnl['pnl_ytd_eur'].sum()
                        st.metric("Total YTD P&L", f"{total_ytd_pnl:,.2f} €", delta=f"{total_ytd_pnl:,.2f} €")
                    with col5:
                        total_all_time_pnl = df_pnl['pnl_all_time_eur'].fillna(0).sum()
                        st.metric("Total All Time P&L", f"{total_all_time_pnl:,.2f} €", delta=f"{total_all_time_pnl:,.2f} €")
                    
                    # 2. Group by Account
                    df_acc = df_pnl.groupby('accounts_name')[['current_value_eur', 'pnl_dtd_eur', 'pnl_wtd_eur', 'pnl_mtd_eur', 'pnl_ytd_eur', 'pnl_all_time_eur', 'pnl_net_all_time_eur']].sum()
                    
                    df_acc = df_acc.rename(columns={
                        'current_value_eur': 'Current Value',
                        'pnl_dtd_eur': 'Daily P&L',
                        'pnl_wtd_eur': 'Weekly P&L',
                        'pnl_mtd_eur': 'Monthly P&L',
                        'pnl_ytd_eur': 'YTD P&L',
                        'pnl_all_time_eur': 'Total P&L',
                        'pnl_net_all_time_eur': 'Total Net P&L'                    
                    })
                    df_acc.index.name = "Account"

                    # Εφαρμογή χρωμάτων σε όλες τις στήλες P&L του df_acc
                    st.dataframe(
                        df_acc.style.map(color_change).format("{:,.2f} €"),
                        #use_container_width=True
                        width="stretch"
                    )

                    # 3. Drill down by Selectbox
                    selected_acc = st.selectbox("Select Account for Details:", df_pnl['accounts_name'].unique())
                    df_details = df_pnl[df_pnl['accounts_name'] == selected_acc]

                    # 1. Φέρνουμε την Ποσότητα από το Holdings
                    query_quantity = f"""
                        SELECT H.Securities_Id, S.Security_Name, H.Quantity 
                        FROM Holdings H
                        JOIN Securities S ON H.Securities_Id = S.Securities_Id
                        WHERE H.Accounts_Id = (SELECT Accounts_Id FROM Accounts WHERE Accounts_Name = '{selected_acc}')
                    """
                    df_qty = pd.read_sql(query_quantity, conn)

                    # 2. Φέρνουμε την τελευταία τιμή από το Historical_Prices (χρησιμοποιώντας window function για σιγουριά)
                    query_prices_old = f"""
                        SELECT DISTINCT ON (HP1.Securities_Id) HP1.Securities_Id, S.Security_Name, HP1.Price_Close as Latest_Price
                        FROM Historical_Prices HP1
                        JOIN Securities S ON HP1.Securities_Id = S.Securities_Id
                        WHERE HP1.Price_Date = (SELECT MAX(HP2.Price_Date) FROM Historical_Prices HP2 WHERE HP2.Securities_Id = HP1.Securities_Id AND HP2.Price_Date <= CURRENT_DATE)
                        ORDER BY HP1.Securities_Id, HP1.Price_Date DESC
                    """
                    query_prices = f"""
                        SELECT H.Securities_Id, S.Security_Name, 
                            (SELECT HP.Price_Close 
                            FROM Historical_Prices HP 
                            WHERE HP.Securities_Id = H.Securities_Id 
                            AND HP.Price_Date <= CURRENT_DATE ORDER BY HP.Price_Date DESC LIMIT 1) AS Latest_Price
                        FROM Holdings H
                        JOIN Securities S ON H.Securities_Id = S.Securities_Id
                        WHERE H.Accounts_Id = (SELECT Accounts_Id FROM Accounts WHERE Accounts_Name = '{selected_acc}')
                    """
                    df_prices = pd.read_sql(query_prices, conn)

                    # 3. Σύνδεση των δεδομένων στο df_details
                    df_details = df_details.merge(df_qty, on='security_name', how='left')
                    df_details = df_details.merge(df_prices, on='security_name', how='left')


                    df_display = df_details[['security_name', 'quantity', 'latest_price', 'current_value_eur', 'pnl_dtd_eur', 'pnl_wtd_eur', 'pnl_mtd_eur', 'pnl_ytd_eur', 'pnl_all_time_eur', 'pnl_net_all_time_eur']].rename(columns={
                        'security_name': 'Security',
                        'quantity': 'Quantity',        
                        'latest_price': 'Latest Price',      
                        'current_value_eur': 'Value (€)',
                        'pnl_dtd_eur': 'Daily P&L',
                        'pnl_wtd_eur': 'Weekly P&L',
                        'pnl_mtd_eur': 'Monthly P&L',
                        'pnl_ytd_eur': 'YTD P&L',
                        'pnl_all_time_eur': 'Total P&L',
                        'pnl_net_all_time_eur': 'Total Net P&L'                    
                    })

                    # Λίστα με τις στήλες που θέλουμε να χρωματίσουμε (όλες εκτός από Security και Value)
                    pnl_cols = ['Daily P&L', 'Weekly P&L', 'Monthly P&L', 'YTD P&L', 'Total P&L', 'Total Net P&L']

                    # Εμφάνιση με st.dataframe για να λειτουργήσει το styling
                    st.dataframe(
                        df_display.style
                        .map(color_change, subset=pnl_cols)
                        .format({col: "{:,.2f} €" for col in pnl_cols + ['Value (€)']}),
                        #use_container_width=True,
                        width="stretch",
                        hide_index=True
                    )
        
                except Exception as e:
                    st.error(f"Σφάλμα: {e}")

                pass

            with tab_movers:
                st.subheader("🔝 Investment Top Movers (Daily)")
                
                # Επιλογή για το πώς θέλουμε να δούμε τους movers
                mover_col = st.radio("Sort by:", ["Daily P&L (€)", "Daily Change (%)"], horizontal=True)
                
                # Υπολογισμός ποσοστιαίας μεταβολής αν δεν υπάρχει ήδη στο df_pnl
                # Υποθέτοντας ότι: Daily Change % = (DTD_PnL / (Current_Value - DTD_PnL)) * 100
                df_pnl['daily_change_pct'] = (df_pnl['pnl_dtd_eur'] / (df_pnl['current_value_eur'] - df_pnl['pnl_dtd_eur'])) * 100

                # Προετοιμασία δεδομένων για εμφάνιση
                df_movers = df_pnl[['security_name', 'accounts_name', 'pnl_dtd_eur', 'daily_change_pct']].copy()
                df_movers.columns = ['Security', 'Account', 'Daily P&L (€)', 'Daily Change (%)']

                col_to_sort = 'Daily P&L (€)' if mover_col == "Daily P&L (€)" else 'Daily Change (%)'

                # Δύο στήλες: Top Gainers και Top Losers
                gainer_col, loser_col = st.columns(2)

                with gainer_col:
                    st.success("📈 Top Gainers")
                    top_gainers = df_movers.sort_values(by=col_to_sort, ascending=False).head(10)
                    st.dataframe(top_gainers.style.format({
                        'Daily P&L (€)': "{:,.2f} €",
                        'Daily Change (%)': "{:,.2f}%"
                    }), hide_index=True, use_container_width=True)

                with loser_col:
                    st.error("📉 Top Losers")
                    top_losers = df_movers.sort_values(by=col_to_sort, ascending=True).head(10)
                    st.dataframe(top_losers.style.format({
                        'Daily P&L (€)': "{:,.2f} €",
                        'Daily Change (%)': "{:,.2f}%"
                    }), hide_index=True, use_container_width=True)

        # INCOME & EXPENSES REPORTS
        elif hist_sub_menu == "Incomes & Expenses":
            #st.write("---")
            tab_income, tab_expenses, tab_net = st.tabs(["💵 Incomes", "💸 Expenses", "📊 Net Totals"])

            with tab_income:
                st.subheader("💵 Total Incomes by Month")
                # --- Income Report ---
                df_inc = df_trans[df_trans['category_type'] == 'Income']
                
                col1, col2 = st.columns(2)
                with col1:
                    st.write("📅 **YTD (Monthly)**")
                    ytd_inc = df_inc[df_inc['Year'] == pd.Timestamp.now().year]
                    st.bar_chart(ytd_inc.groupby('Month')['amount_eur'].sum())
                    
                with col2:
                    st.write("🌍 **All Time (Yearly)**")
                    st.line_chart(df_inc.groupby('Year')['amount_eur'].sum())

            with tab_expenses:
                st.subheader("💸 Total Expenses by Month")
                # --- Expenses Report ---
                df_exp = df_trans[df_trans['category_type'] == 'Expense']
                
                col1, col2 = st.columns(2)
                with col1:
                    st.write("📅 **YTD (Monthly)**")
                    ytd_exp = df_exp[df_exp['Year'] == pd.Timestamp.now().year]
                    st.bar_chart(ytd_exp.groupby('Month')['amount_eur'].sum(), color="#FF4B4B")
                    
                with col2:
                    st.write("🌍 **All Time (Yearly)**")
                    st.line_chart(df_exp.groupby('Year')['amount_eur'].sum(), color="#FF4B4B")

            with tab_net:
                st.write("📊 **Net Totals (Consolidated)**")
                # Pivot table για σύγκριση
                net_df = df_trans.groupby(['Year', 'category_type'])['amount_eur'].sum().unstack(fill_value=0)
                net_df['Net'] = net_df.get('Income', 0) - net_df.get('Expense', 0)
                
                st.dataframe(
                    net_df.style.map(color_change, subset=['Net']).format("{:,.2f} €"),
                    width="stretch"
                )
                st.line_chart(net_df['Net'])



    # --- ⚙️ MARKET DATA ---
    elif menu == "🌍 Market Data":
        st.title("Market Data")
        t1, t2 = st.tabs(["FX Rates", "Security Prices"])

        # Βοηθητικά δεδομένα για τα Dropdowns (τα φορτώνουμε μία φορά)
        df_curr_list = pd.read_sql("SELECT Currencies_Id, Currencies_ShortName FROM Currencies", conn)
        
        curr_options = df_curr_list.set_index('currencies_id')['currencies_shortname'].to_dict()
        
        with t1: # Historical FX Rates
            df = pd.read_sql("SELECT * FROM Historical_FX ORDER BY FX_Date DESC, Base_Currency_Id ASC", conn)
            edited_hfx = st.data_editor(df, num_rows="dynamic", key="set_hfx", column_config={
                "base_currency_id": st.column_config.SelectboxColumn("Base Currency", options=list(curr_options.keys()), format_func=lambda x: curr_options.get(x, "Unknown")),
                "target_currency_id": st.column_config.SelectboxColumn("Target Currency", options=list(curr_options.keys()), format_func=lambda x: curr_options.get(x, "Unknown"))
            })
            save_changes_no_serial(df, edited_hfx, "Historical_FX", "fx_date")

            # --- Γράφημα Ιστορικότητας Ισοτιμιών ---
            if not df.empty:
                st.subheader("📈 Διάγραμμα Ισοτιμιών")
                
                # Δημιουργία στήλης με το όνομα του ζεύγους (π.χ. EUR/USD) για το UI
                df_plot = df.copy()
                df_plot['Pair'] = df_plot.apply(
                    lambda row: f"{curr_options.get(row['base_currency_id'], '??')}/{curr_options.get(row['target_currency_id'], '??')}", 
                    axis=1
                )

                # Επιλογή ζεύγους για προβολή στο γράφημα
                available_pairs = df_plot['Pair'].unique()
                selected_pair = st.selectbox("Επιλέξτε ζεύγος για προβολή:", available_pairs)

                # Φιλτράρισμα δεδομένων για το επιλεγμένο ζεύγος
                chart_data = df_plot[df_plot['Pair'] == selected_pair].sort_values('fx_date')

                # Προβολή γραφήματος
                if not chart_data.empty:
                    st.line_chart(
                        data=chart_data, 
                        x='fx_date', 
                        y='fx_rate', 
                        use_container_width=True
                    )
                else:
                    st.info("Δεν υπάρχουν δεδομένα για το επιλεγμένο ζεύγος.")

            # --- UI elements για το Download ---
            st.subheader("🔄 Ενημέρωση Συναλλαγματικών Ισοτιμιών")

            col1, col2 = st.columns([2, 1])

            with col1:
                # Επιλογή περιόδου (Yahoo Finance periods)
                period_options = {
                    "1 Ημέρα": "1d",
                    "5 Ημέρες": "5d",
                    "1 Μήνας": "1mo",
                    "6 Μήνες": "6mo",
                    "1 Έτος": "1y",
                    "5 Έτη": "5y",
                    "10 Έτη": "10y",
                    "15 Έτη": "15y",
                    "20 Έτη": "20y",
                    "25 Έτη": "25y",
                    "30 Έτη": "30y",
                    "Όλα": "max"
                }

                selected_label = st.selectbox("Επιλέξτε χρονικό διάστημα:", list(period_options.keys()), index=1)
                ts_period = period_options[selected_label]

            with col2:
                st.write(" ") # Padding
                st.write(" ") 
                # Το κουμπί που καλεί τη συνάρτηση
                if st.button("🚀 Λήψη Ισοτιμιών"):
                    with st.spinner("Παρακαλώ περιμένετε, η διαδικασία είναι σε εξέλιξη..."):
                        download_historical_fx(ts_period)
                        st.balloons() # Εφέ επιτυχίας
                        st.rerun() 
                        
        with t2: # Historical Prices    

            # 1. Επιλογή Security (Dropdown)
            df_inv_secs = pd.read_sql("SELECT S.Securities_Id, S.Security_Name, (SELECT COUNT(HP.*) FROM Historical_Prices HP WHERE HP.Securities_Id = S.Securities_Id) NoOfRecords, (SELECT COALESCE(MAX(HP.Price_Date),'1900-01-01') FROM Historical_Prices HP WHERE HP.Securities_Id = S.Securities_Id) MaxDate FROM Securities S ORDER BY S.Security_Name ASC", conn)

            if df_inv_secs.empty:
                st.warning("⚠️ Δεν βρέθηκαν Securities. Ορίστε ένα Security στις Ρυθμίσεις.")
            else:
                selected_inv_sec = st.selectbox(
                    "Επιλέξτε Security:", 
                    df_inv_secs.to_dict('records'), 
                     format_func=lambda x: f"{x['security_name']} ({x['noofrecords']:,.0f}) ({x['maxdate']})"
                )
                inv_sec_id = selected_inv_sec['securities_id']

            # Φόρτωση τιμών μόνο για το επιλεγμένο Security
            df_hpr_tx = pd.read_sql(f"SELECT * FROM Historical_Prices WHERE Securities_Id = {inv_sec_id} ORDER BY Price_Date DESC", conn)

            edited_hpr_tx = st.data_editor(
                df_hpr_tx,
                num_rows="dynamic",
                key=f"inv_hpr_editor_{inv_sec_id}",
                #use_container_width=True,
                width="stretch",
            )

            save_changes_mid(
                edited_hpr_tx, 
                "Historical_Prices", 
                id_cols=["securities_id", "price_date"], # Σύνθετο κλειδί για το ON CONFLICT
                filter_col="securities_id",              # Περιορίζουμε το DELETE
                filter_val=inv_sec_id                    # μόνο για το τρέχον security
            )


            # --- UI elements για το Download ---
            st.subheader("🔄 Ενημέρωση Αποτιμήσεων")

            col1, col2 = st.columns([2, 1])

            with col1:
                # Επιλογή περιόδου (Yahoo Finance periods)
                period_options = {
                    "1 Ημέρα": "1d",
                    "5 Ημέρες": "5d",
                    "1 Μήνας": "1mo",
                    "6 Μήνες": "6mo",
                    "1 Έτος": "1y",
                    "5 Έτη": "5y",
                    "10 Έτη": "10y",
                    "15 Έτη": "15y",
                    "20 Έτη": "20y",
                    "25 Έτη": "25y",
                    "30 Έτη": "30y",
                    "Όλα": "max"
                }
                selected_label = st.selectbox("Επιλέξτε χρονική περίοδο:", list(period_options.keys()), index=1)
                ts_period = period_options[selected_label]

            with col2:
                st.write(" ") # Padding
                st.write(" ") 
                # Το κουμπί που καλεί τη συνάρτηση
                if st.button("🚀 Λήψη Αποτιμήσεων"):
                    with st.spinner("Παρακαλώ περιμένετε, η διαδικασία είναι σε εξέλιξη..."):
                        download_historical_prices_from_yahoo(ts_period)
                        st.balloons() # Εφέ επιτυχίας
                        st.rerun() 
     

    # --- ⚙️ ΡΥΘΜΙΣΕΙΣ ---
    elif menu == "🔧 Settings":
        st.title("System Settings")
        t1, t2, t3, t4, t5, t6 = st.tabs(["Currencies", "Institutions", "Categories", "Securities", "Payees", "Accounts"])
        
        # Βοηθητικά δεδομένα για τα Dropdowns (τα φορτώνουμε μία φορά)
        df_curr_list = pd.read_sql("SELECT Currencies_Id, Currencies_ShortName FROM Currencies", conn)
        df_inst_list = pd.read_sql("SELECT FinancialInstitutions_Id, FinancialInstitutions_Name FROM FinancialInstitutions", conn)
        df_sec_list = pd.read_sql("SELECT Securities_Id, Security_Name FROM Securities", conn)
        
        curr_options = df_curr_list.set_index('currencies_id')['currencies_shortname'].to_dict()
        inst_options = df_inst_list.set_index('financialinstitutions_id')['financialinstitutions_name'].to_dict()
        sec_options = df_sec_list.set_index('securities_id')['security_name'].to_dict()

        with t1: # Currencies
            df = pd.read_sql("SELECT * FROM Currencies ORDER BY Currencies_Id", conn)
            save_changes(df, st.data_editor(df, num_rows="dynamic", key="set_curr"), "Currencies", "currencies_id")

        with t2: # Institutions
            #CREATE TYPE Institution_Type AS ENUM ('Bank', 'Credit Union', 'Insurance', 'Pension Fund', 'Broker', 'Crypto Exchange', 'Internal', 'Other');

            
            df = pd.read_sql("SELECT * FROM FinancialInstitutions ORDER BY FinancialInstitutions_Id", conn)

            edited_inst = st.data_editor(df, num_rows="dynamic", key="set_inst", column_config={
                "financialinstitutions_type": st.column_config.SelectboxColumn("Institution Type", options=['Bank', 'Credit Union', 'Insurance', 'Pension Fund', 'Broker', 'Crypto Exchange', 'Internal', 'Other'])
            })
            save_changes(df, edited_inst, "FinancialInstitutions", "financialinstitutions_id")
        with t3: # Categories
            df = pd.read_sql("SELECT * FROM Categories ORDER BY Categories_Id", conn)
            edited_cat = st.data_editor(df, num_rows="dynamic", key="set_cat", column_config={
                "category_type": st.column_config.SelectboxColumn("Type", options=['Income', 'Expense', 'Transfer', 'Investment_Buy', 'Investment_Sell', 'Dividend', 'Interest', 'Tax', 'Fee'])
            })
            save_changes(df, edited_cat, "Categories", "categories_id")

        with t4: # Securities
            df = pd.read_sql("SELECT * FROM Securities ORDER BY Security_Name", conn)
            edited_sec = st.data_editor(df, num_rows="dynamic", key="set_sec", column_config={
                "security_type": st.column_config.SelectboxColumn("Type", options=['Stock', 'ETF', 'Bond', 'CD', 'Emp. Stock Opt.', 'FX Spot', 'Market Index', 'Mutual Fund', 'Crypto', 'Option', 'Commodity', 'PF_Unit', 'Other']),
                "currencies_id": st.column_config.SelectboxColumn("Currency", options=list(curr_options.keys()), format_func=lambda x: curr_options.get(x, "Unknown"))
            })
            save_changes(df, edited_sec, "Securities", "securities_id")
            
        with t5: # Payees
            df = pd.read_sql("SELECT * FROM Payees ORDER BY Payees_Id", conn)
            save_changes(df, st.data_editor(df, num_rows="dynamic", key="set_pay"), "Payees", "payees_id")

        with t6: # Accounts
            df = pd.read_sql("SELECT * FROM Accounts ORDER BY Accounts_Id", conn)
            edited_acc = st.data_editor(df, num_rows="dynamic", key="set_acc", column_config={
                "accounts_type": st.column_config.SelectboxColumn("Account Type", options=['Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']),
                "institution_id": st.column_config.SelectboxColumn("Institution", options=list(inst_options.keys()), format_func=lambda x: inst_options.get(x, "Unknown")),
                "currencies_id": st.column_config.SelectboxColumn("Currency", options=list(curr_options.keys()), format_func=lambda x: curr_options.get(x, "Unknown")),
                "is_active": st.column_config.CheckboxColumn("Active")
            })
            save_changes(df, edited_acc, "Accounts", "accounts_id")
            
    conn.close()
except Exception as e:
    st.error(f"Error: {e}")

