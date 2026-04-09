import quiffen
import psycopg2
import csv
from datetime import datetime

qif_file_path='Angelos Quicken Data.QIF'

# Σύνδεση με τη βάση δεδομένων Finance
conn = psycopg2.connect(
    dbname="Finance", user="admin", password="31.12.1969",
    host="192.168.4.20", port="5432"
)
cur = conn.cursor()

# --- 1. ΑΠΕΝΕΡΓΟΠΟΙΗΣΗ TRIGGERS ΣΤΗΝ ΑΡΧΗ ---
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Απενεργοποίηση triggers για ταχύτητα...")
cur.execute("ALTER TABLE Bank_Transactions DISABLE TRIGGER trg_update_balance;")
cur.execute("ALTER TABLE Investment_Transactions DISABLE TRIGGER trg_update_holdings;")
conn.commit()

# Καθαρισμός πινάκων πριν την εισαγωγή
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Καθαρισμός βάσης δεδομένων...")
tables_to_clean = [
#    "Bank_Transaction_Splits", "Bank_Transactions", "Investment_Transactions", "Holdings", "Securities"
    "Bank_Transaction_Splits", "Bank_Transactions", "Investment_Transactions", "Holdings", "Categories"
]

for table in tables_to_clean:
    cur.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;")

conn.commit()
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Η βάση είναι καθαρή. Ξεκινάει η εισαγωγή...")


def clean_id(val):
    """Μετατρέπει (7,) ή [7] ή 7 σε καθαρό 7. Επιστρέφει None αν το val είναι None."""
    if val is None:
        return None
    if isinstance(val, (tuple, list)):
        return val[0]
    return val

def get_or_create_id(table, id_col, name_col, name_val, extra_cols=None):
    """Βοηθητική συνάρτηση για ανάκτηση ή δημιουργία ID εγγραφής."""
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name_val,))
    result = cur.fetchone()
    if result:
        return result[0]
    
    # Δημιουργία αν δεν υπάρχει
    if extra_cols:
        cols = [name_col] + list(extra_cols.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        vals = [name_val] + list(extra_cols.values())
        cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING {id_col}", vals)
    else:
        cur.execute(f"INSERT INTO {table} ({name_col}) VALUES (%s) RETURNING {id_col}", (name_val,))
    
    new_id = cur.fetchone()[0]
    conn.commit()
    return new_id

def get_or_create_category_id(table, id_col, name_col, name_val, cat_type='Expense'):
    if not name_val: return None
    name_val = name_val.name if hasattr(name_val, 'name') else str(name_val)
    
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name_val,))
    row = cur.fetchone()
    if row: return row[0]
    
    # Εδώ προσθέτουμε το category_type για να μη χτυπάει το NOT NULL
    cur.execute(
        f"INSERT INTO {table} ({name_col}, category_type) VALUES (%s, %s) RETURNING {id_col}",
        (name_val, cat_type)
    )
    return cur.fetchone()[0]

def get_or_create_category_recursive(full_name, cat_type='Expense'):
    if not full_name: return None
    
    parts = full_name.split(':')
    parent_id = None
    
    for part in parts:
        part = part.strip()
        if not part: continue
        
        # Αναζήτηση
        if parent_id is None:
            cur.execute("SELECT Categories_Id FROM Categories WHERE Categories_Name = %s AND Parent_Category_Id IS NULL", (part,))
        else:
            cur.execute("SELECT Categories_Id FROM Categories WHERE Categories_Name = %s AND Parent_Category_Id = %s", (part, parent_id))
        
        row = cur.fetchone()
        if row:
            # Σημαντικό: Το row είναι tuple (5,), εμείς θέλουμε το 5
            current_id = row
        else:
            # Εισαγωγή με το cat_type που βρήκαμε (Income/Expense)
            cur.execute("""
                INSERT INTO Categories (Categories_Name, Parent_Category_Id, Category_Type)
                VALUES (%s, %s, %s) RETURNING Categories_Id
            """, (part, parent_id, cat_type))
            current_id = cur.fetchone()
            
        parent_id = current_id # Ο τρέχων γίνεται Parent για το επόμενο split (:)
        
    return parent_id # Επιστρέφει το ID της τελικής υποκατηγορίας



def get_id(table, id_col, name_col, name_val):
    """Βοηθητική συνάρτηση για ανάκτηση ID εγγραφής."""
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name_val,))
    result = cur.fetchone()
    if result:
        return result[0]
    



# Φόρτωση και parsing του αρχείου QIF
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Φόρτωση και parsing του αρχείου QIF: " + qif_file_path)
qif = quiffen.Qif.parse(qif_file_path, day_first=False, encoding='latin-1')


print("Διαθέσιμα πεδία στο qif:", qif.__dict__.keys())
exit


print("\n--- Deep Inspection of QIF Object ---")
# 1. Δες όλα τα attributes του qif αντικειμένου
print(f"Attributes: {dir(qif)}")

# 2. Έλεγχος του εσωτερικού dictionary (εδώ κρύβονται συνήθως τα αντικείμενα)
if hasattr(qif, '_category_data'):
    print("\n--- Category Data Found ---")
    for name, obj in qif._category_data.items():
        print(f"Category: {name} | Type: {type(obj)}")
        # Αν είναι αντικείμενο Pydantic, δες τα περιεχόμενα
        if hasattr(obj, 'model_dump'):
            print(f"Data: {obj.model_dump()}")
        else:
            print(f"Vars: {vars(obj)}")
else:
    print("\nNo '_category_data' attribute found.")

print("\n--- Αναλυτική Επιθεώρηση Κατηγοριών ---")
# Το quiffen αποθηκεύει τα αντικείμενα στο _category_list ή category_data
# Δοκιμάζουμε το category_list που είναι η standard Pydantic λίστα
for cat_obj in getattr(qif, 'category_list', []):
    print(f"Name: {cat_obj.name}")
    print(f"Income: {getattr(cat_obj, 'income', 'N/A')}")
    print(f"Tax: {getattr(cat_obj, 'tax_related', 'N/A')}")
    # Εκτύπωση όλων των πεδίων του Pydantic model
    print(f"All Fields: {cat_obj.model_dump()}") 
    print("-" * 30)

# Αν η παραπάνω λίστα είναι άδεια, δοκιμάστε αυτό:
if not getattr(qif, 'category_list', None):
    print("Η category_list είναι άδεια. Δοκιμή μέσω category_data...")
    cat_data = getattr(qif, 'category_data', {})
    for name, obj in cat_data.items():
        print(f"Category: {name} | Data: {obj.model_dump() if hasattr(obj, 'model_dump') else vars(obj)}")



# 1. Εισαγωγή Κατηγοριών
now = datetime.now()
#print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Εισαγωγή Κατηγοριών...")

#for cat_name, cat_obj in qif.categories.items():
#    # Δοκιμάζουμε να δούμε αν είναι Expense, αλλιώς default σε 'Expense' 
#    # καθώς οι περισσότερες κατηγορίες στο Quicken είναι έξοδα.
#    cat_type = 'Income' if getattr(cat_obj, 'income', False) else 'Expense'
#    
#    get_or_create_category_id("Categories", "categories_id", "categories_name", cat_name, 
#                     {"category_type": cat_type})

# Προσπαθούμε να βρούμε το λεξικό με τα αντικείμενα (συχνά ονομάζεται _categories ή category_list)
# Αν δεν υπάρχει, χρησιμοποιούμε την ασφαλή μέθοδο get_category

print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Χειροκίνητη Εισαγωγή Κατηγοριών από το αρχείο...")

with open(qif_file_path, 'r', encoding='latin-1') as f:
    current_cat = None
    current_type = 'Expense'
    in_category_section = False
    
    for line in f:
        line = line.strip()
        print("WORKING ON Line: " + line)
        if not line: continue
        
        # Ενεργοποίηση/Απενεργοποίηση ανάγνωσης κατηγοριών
        if line.startswith('!Type:Cat'):
            in_category_section = True
            continue
        elif line.startswith('!Type:'):
            in_category_section = False
            continue

        if in_category_section:
            if line.startswith('N'):
                current_cat = line[1:]
            elif line.startswith('I'):
                current_type = 'Income'
            elif line == '^':
                print("WORKING ON Category: " + current_cat + " (" + current_type + ")")
                if current_cat:
                    get_or_create_category_recursive(current_cat, cat_type=current_type)
                # Reset για την επόμενη κατηγορία
                current_cat = None
                current_type = 'Expense'




# 2. Εισαγωγή Securities
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Εισαγωγή Securities και Αποτιμήσεων...")
for security in qif.securities.values():
    name = security.name
    ticker = security.symbol
    sectype = security.type  # Αυτό είναι το πεδίο που είδαμε στο dict σου
    
    print("WORKING ON Security: " + name + " (" + ticker + ") - " + sectype)

    
    # Εδώ συνεχίζεις με την εισαγωγή στη βάση σου...
    s_id = get_or_create_id("Securities", "securities_id", "ticker", ticker, 
                           {"security_name": name, "security_type": sectype})
    c_sec_id = clean_id(s_id)



# 3. Εισαγωγή Λογαριασμών και Συναλλαγών
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Εισαγωγή Λογαριασμών και Συναλλαγών...")
for acc_name, acc_obj in qif.accounts.items():
    # Προσπάθεια ανάκτησης του νομίσματος από το αντικείμενο του λογαριασμού
    # Αν δεν υπάρχει, ορίζουμε 'EUR' ως default
    qif_currency = getattr(acc_obj, 'currency', 'EUR')
    
    # Δημιουργία/Ανάκτηση του Currency ID στον πίνακα Currencies
    curr_id = get_or_create_id('Currencies', 'Currencies_Id', 'Currencies_ShortName', qif_currency, 
                               {'Currencies_Name': qif_currency})
    
    # Μετά δημιουργούμε τον λογαριασμό χρησιμοποιώντας το σωστό curr_id
    acc_id = get_or_create_id('Accounts', 'Accounts_Id', 'Accounts_Name', acc_name, 
                              {'Accounts_Type': 'Checking', 'Currencies_Id': curr_id})


    # Επεξεργασία Συναλλαγών ανά τύπο (Bank, Invst κλπ)
    for tx_list in acc_obj.transactions.values():
        for tx in tx_list:
            # Καθαρισμός του Account ID
            c_acc_id = clean_id(acc_id)

            #if hasattr(tx, 'payee'):  # Τραπεζική Συναλλαγή
            #    p_id = get_or_create_id("Payees", "payees_id", "payees_name", tx.payee) if tx.payee else None
            #    c_payee_id = clean_id(p_id)
            #    is_cleared = True if tx.cleared in ['X', '*', 'R'] else False
            #    
            #    cur.execute("""
            #        INSERT INTO Bank_Transactions (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared)
            #        VALUES (%s, %s, %s, %s, %s, %s)
            #    """, (c_acc_id, tx.date, c_payee_id, tx.memo or tx.payee, tx.amount, is_cleared))

            # Changes for Split on 2026/04/02
            if hasattr(tx, 'payee'): # Τραπεζική Συναλλαγή
                p_id = get_or_create_id("Payees", "payees_id", "payees_name", tx.payee) if tx.payee else None
                c_payee_id = clean_id(p_id)
                is_cleared = True if tx.cleared in ['X', '*', 'R'] else False
                
                # 1. Εισαγωγή στην Bank_Transactions
                cur.execute("""
                    INSERT INTO Bank_Transactions (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING Bank_Transactions_Id
                """, (c_acc_id, tx.date, c_payee_id, tx.memo or tx.payee, tx.amount, is_cleared))
                
                bt_id = cur.fetchone()[0] # Παίρνουμε την τιμή του ID

                # 2. Εισαγωγή των Splits
                if tx.splits:
                    for split in tx.splits:
                        cat_id = None
                        if split.category:
                            # Προτεραιότητα στο hierarchy για να πάρουμε το πλήρες μονοπάτι (π.χ. Healthcare:Dental)
                            if hasattr(split.category, 'hierarchy') and split.category.hierarchy:
                                cat_name = split.category.hierarchy
                            elif hasattr(split.category, 'name'):
                                cat_name = split.category.name
                            else:
                                cat_name = str(split.category)
                            
                            # Χρήση της αναδρομικής συνάρτησης για σωστή τοποθέτηση στην ιεραρχία
                            cat_id = get_or_create_category_recursive(cat_name, cat_type='Expense')
                        
                        cur.execute("""
                            INSERT INTO Bank_Transaction_Splits (Bank_Transactions_Id, Categories_Id, Amount, Memo)
                            VALUES (%s, %s, %s, %s)
                        """, (bt_id, clean_id(cat_id), split.amount, split.memo))

                else:
                    # Fallback αν δεν υπάρχουν splits
                    cat_id = None

                    # Δοκίμασε αυτό το print για να δεις όλη τη δομή
                    #print(f"DEBUG: Category object: {tx.category} | Type: {type(tx.category)}")
                    
                    if hasattr(tx, 'category') and tx.category:
                        # Χρήση της ιδιότητας hierarchy που περιέχει το πλήρες μονοπάτι
                        if hasattr(tx.category, 'hierarchy') and tx.category.hierarchy:
                            cat_name = tx.category.hierarchy
                        elif hasattr(tx.category, 'name'):
                            cat_name = tx.category.name
                        else:
                            cat_name = str(tx.category)
                        
                        # 2. Χρήση της RECURSIVE συνάρτησης
                        cat_id = get_or_create_category_recursive(cat_name, cat_type='Expense')
 
                        print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Εισαγωγή Συναλλαγής με κατηγορία " + cat_name)
 
                    cur.execute("""
                        INSERT INTO Bank_Transaction_Splits (Bank_Transactions_Id, Categories_Id, Amount, Memo)
                        VALUES (%s, %s, %s, %s)
                    """, (bt_id, clean_id(cat_id), tx.amount, tx.memo))
                    
            elif hasattr(tx, 'security'):  # Επενδυτική Συναλλαγή
                # Χρησιμοποιούμε το security name ως ticker αν το ticker λείπει
                # και το περιορίζουμε για σιγουριά, αν και κάναμε ALTER
                ticker_val = (tx.security or "UNKNOWN")[:255]
                
                #s_id = get_or_create_id("Securities", "securities_id", "ticker", ticker_val, 
                #                       {"security_name": ticker_val, "security_type": 'Stock'})
                
                #s_id = get_or_create_id("Securities", "securities_id", {"security_name": ticker_val})
                
                # Σωστή κλήση: Πίνακας, ID Column, Column Name για αναζήτηση, Τιμή για αναζήτηση
                s_id = get_id("Securities", "securities_id", "security_name", ticker_val)

                c_sec_id = clean_id(s_id)
                
                # Πιο πλήρες Mapping Actions για Quicken
                action_map = {
                    'Buy': 'Buy', 
                    'BuyX': 'Buy', 
                    'Sell': 'Sell', 
                    'SellX': 'Sell', 
                    'Div': 'Dividend', 
                    'DivX': 'Dividend', 
                    'Dividend': 'Dividend',
                    'ReinvDiv': 'Reinvest',
                    'ReinvInt': 'Reinvest',
                    'Splt': 'Split',
                    'StkSplit': 'Split',
                    'ShrsIn': 'ShrIn',
                    'IntInc': 'IntInc',
                    'IntIncX': 'IntInc',
                    'ShrsOut': 'ShrOut',
                    'Cash': 'CashIn',
                    'XIn': 'CashIn',
                    'RtrnCap': 'RtrnCap',
                    'WithdrwX': 'CashOut',
                    'XOut': 'CashOut',
                    'MiscExpX': 'MiscExp',
                    'Grant': 'Grant',
                    'Vest': 'Vest',
                    'ExercisX': 'Exercise',
                    'Expire': 'Expire'
                }
                # Μετατροπή του tx.action σε σωστό Case για το Enum μας
                raw_action = str(tx.action).strip()
                my_action = action_map.get(raw_action, 'Buy')
                
                # Διασφάλιση ότι οι τιμές δεν είναι None για να μην κρασάρει το SQL
                qnt = tx.quantity if tx.quantity else 0
            #    prc = tx.price if tx.price se 0
                prc = tx.price if tx.price and my_action != 'Reinvest' else 0
                comm = tx.commission if tx.commission else 0
                amt = tx.amount if tx.amount else 0

                cur.execute("""
                    INSERT INTO Investment_Transactions 
                    (Accounts_Id, Securities_Id, Date, Action, Quantity, Price_Per_Share, Commission, Total_Amount, Description)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (c_acc_id, c_sec_id, tx.date, my_action, qnt, prc, comm, amt, tx.memo))

        conn.commit()
#conn.commit()


# --- 4. Εισαγωγή Αποτιμήσεων
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Εισαγωγή Αποτιμήσεων")

# Διαβάζουμε το αρχείο QIF
# Χρησιμοποιούμε latin-1 για να συμβαδίζουμε με το Quiffen
with open(qif_file_path, 'r', encoding='latin-1') as f:
    reader = csv.reader(f)
    
    for row in reader:
        # Οι γραμμές τιμών έχουν πάντα 3 στήλες
        if len(row) == 3:
            ticker = row[0]       # π.χ. ADMr.xt
            price_value = row[1]  # π.χ. 2.33
            raw_date = row[2]     # π.χ. " 2/12'24"
            
            try:
                # 1. Καθαρισμός ημερομηνίας: " 3/ 1'24" -> "3/1/24"
                # Αφαιρούμε κενά και αλλάζουμε το ' σε /
                clean_date = raw_date.strip().replace(" ", "").replace("'", "/")
                
                # 2. Μετατροπή σε ημερομηνία Python
                date_obj = datetime.strptime(clean_date, "%m/%d/%y")
                
                # 3. Εύρεση ID του Security από τη βάση σου
                s_id = get_id("Securities", "securities_id", "ticker", ticker)
                c_sec_id = clean_id(s_id)
                
                # 4. Εισαγωγή στη βάση
                cur.execute("""
                    INSERT INTO Historical_Prices (Securities_Id, Price_Date, Price_Close)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (Securities_Id, Price_Date) 
                    DO NOTHING
                """, (c_sec_id, date_obj.date(), float(price_value)))

                
            except (ValueError, IndexError) as e:
                # Αγνοούμε γραμμές που δεν είναι τιμές (π.χ. επικεφαλίδες)
                continue

conn.commit()



# --- 5. ΜΑΖΙΚΗ ΕΝΗΜΕΡΩΣΗ ΥΠΟΛΟΙΠΩΝ BANK ACCOUNTS ΣΤΟ ΤΕΛΟΣ ---
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Υπολογισμός και ενημέρωση τελικών υπολοίπων (Account_Balance) for Bank Accounts...")
# Ενημερώνουμε όλα τα υπόλοιπα με βάση το άθροισμα των συναλλαγών
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

# --- 6. ΜΑΖΙΚΗ ΕΝΗΜΕΡΩΣΗ ΥΠΟΛΟΙΠΩΝ PENSION ACCOUNTS ΣΤΟ ΤΕΛΟΣ ---
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Υπολογισμός και ενημέρωση τελικών υπολοίπων (Account_Balance) for Pension Accounts...")
# Ενημερώνουμε όλα τα υπόλοιπα με βάση το άθροισμα των συναλλαγών
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

# --- 7. ΕΝΗΜΕΡΩΣΗ HOLDINGS ---
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Ενημέρωση πίνακα Holdings από τις επενδυτικές συναλλαγές...")
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

# --- 8. ΕΠΑΝΕΝΕΡΓΟΠΟΙΗΣΗ TRIGGERS ---
now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Επανενεργοποίηση triggers...")
cur.execute("ALTER TABLE Bank_Transactions ENABLE TRIGGER trg_update_balance;")
cur.execute("ALTER TABLE Investment_Transactions ENABLE TRIGGER trg_update_holdings;")
conn.commit()

cur.close()

conn.close()

now = datetime.now()
print(now.strftime("%Y-%m-%d %H:%M:%S") + " - Η εισαγωγή και ο συγχρονισμός ολοκληρώθηκαν!")

