CREATE TYPE Institution_Type AS ENUM ('Bank', 'Credit Union', 'Insurance', 'Pension Fund', 'Broker', 'Crypto Exchange', 'Internal', 'Other');
CREATE TYPE Account_Type AS ENUM ('Cash', 'Checking', 'Savings', 'Credit Card', 'Brokerage', 'Pension', 'Other Investment', 'Margin', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other');
CREATE TYPE Security_Type AS ENUM ('Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Option', 'Commodity', 'PF_Unit');
CREATE TYPE Transaction_Category_Type AS ENUM ('Income', 'Expense', 'Transfer', 'Investment_Buy', 'Investment_Sell', 'Dividend', 'Interest', 'Tax', 'Fee');
CREATE TYPE Investment_Action AS ENUM ('Buy', 'Sell', 'Dividend', 'Reinvest', 'Split', 'ShrIn', 'ShrOut', 'IntInc', 'CashIn', 'CashOut', 'Vest', 'Expire', 'Grant', 'Exercise', 'MiscExp', 'RtrnCap');
CREATE SEQUENCE IF NOT EXISTS transfer_id_seq START 1 INCREMENT 1;

CREATE TABLE Currencies (
    Currencies_Id SERIAL PRIMARY KEY,
    Currencies_ShortName CHAR(3) UNIQUE NOT NULL, -- EUR, USD, GBP, BTC
    Currencies_Name VARCHAR(100) NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_currency_id ON Currencies(Currencies_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_currency_name ON Currencies(Currencies_ShortName);

CREATE TABLE FinancialInstitutions (
    FinancialInstitutions_Id SERIAL PRIMARY KEY,
    FinancialInstitutions_Name VARCHAR(100) NOT NULL,
    FinancialInstitutions_Type Institution_Type NOT NULL,
    BIC_Code VARCHAR(11),
    Contact VARCHAR(100),
    Phone VARCHAR(20),
    Email VARCHAR(100),
    Website VARCHAR(255),
    Notes TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_institution_id ON FinancialInstitutions(FinancialInstitutions_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_institution_name ON FinancialInstitutions(FinancialInstitutions_Name);

CREATE TABLE Categories (
    Categories_Id SERIAL PRIMARY KEY,
    Categories_Name VARCHAR(100) NOT NULL,
    Parent_Category_Id INTEGER REFERENCES Categories(Categories_Id) ON DELETE CASCADE,
    Category_Type Transaction_Category_Type NOT NULL,
    UNIQUE(Categories_Name, Parent_Category_Id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_category_id ON Categories(Categories_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_category_name ON Categories(Categories_Name, Parent_Category_Id);

CREATE TABLE Securities (
    Securities_Id SERIAL PRIMARY KEY,
    Ticker VARCHAR(255) UNIQUE NOT NULL,          -- π.χ. 'AAPL', 'BTC-USD', 'EURUSD=X'
    Security_Name VARCHAR(255) UNIQUE NOT NULL,
    Security_Type Security_Type NOT NULL,
    Currencies_Id INTEGER REFERENCES Currencies(Currencies_Id),
    Sector VARCHAR(50),
    Is_Active BOOLEAN DEFAULT TRUE,
    Yahoo_Ticker VARCHAR(30)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_security_id ON Securities(Securities_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_security_name ON Securities(Security_Name);

CREATE TABLE Accounts (
    Accounts_Id SERIAL PRIMARY KEY,
    Accounts_Name VARCHAR(100) NOT NULL,
    Accounts_Type Account_Type NOT NULL,
    Institution_Id INTEGER REFERENCES FinancialInstitutions(FinancialInstitutions_Id),
    IBAN VARCHAR(34),
    Currencies_Id INTEGER REFERENCES Currencies(Currencies_Id),
    Account_Balance NUMERIC(28, 18) DEFAULT 0, -- Υψηλή ακρίβεια για Crypto/Satoshi
    Is_Active BOOLEAN DEFAULT TRUE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_id ON Accounts(Accounts_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_name ON Accounts(Accounts_Name);

CREATE TABLE Holdings (
    Holdings_Id SERIAL PRIMARY KEY,
    Accounts_Id INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Securities_Id INTEGER REFERENCES Securities(Securities_Id),
    Quantity NUMERIC(28, 18) NOT NULL DEFAULT 0,
    Avg_Purchase_Price NUMERIC(20, 8),               -- Στο νόμισμα του Security
    Last_Update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(Accounts_Id, Securities_Id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holding_id ON Holdings(Holdings_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_holding_accsec ON Holdings(Accounts_Id, Securities_Id);

-- 1. Δημιουργία Πίνακα Payees
CREATE TABLE Payees (
    Payees_Id SERIAL PRIMARY KEY,
    Payees_Name VARCHAR(255) UNIQUE NOT NULL,
    Default_Categories_Id INTEGER REFERENCES Categories(Categories_Id), -- Προαιρετικό: Αυτόματη κατηγορία
    Notes TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payee_id ON Payees(Payees_Id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payee_name ON Payees(Payees_Name);

-- Κύριος πίνακας (Bank & Credit Cards)
CREATE TABLE Bank_Transactions (
    Bank_Transactions_Id SERIAL PRIMARY KEY,
    Accounts_Id INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Date DATE NOT NULL,              -- Εδώ θα μπαίνουν και μελλοντικές ημερομηνίες για δόσεις
    Payees_Id INTEGER REFERENCES Payees(Payees_Id),
    Description TEXT,                -- π.χ. "Αγορά Τηλεόρασης - Δόση 1/12"
    Total_Amount NUMERIC(28, 18),    -- Συνολικό ποσό κίνησης
    Cleared BOOLEAN DEFAULT FALSE,    -- FALSE για τις μελλοντικές δόσεις
	Transfer_Id INTEGER NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_id ON Bank_Transactions(Bank_Transactions_Id);
CREATE INDEX IF NOT EXISTS idx_transfer_id ON Bank_Transactions(Transfer_Id) WHERE Transfer_Id IS NOT NULL;

-- Πίνακας Splits (Εδώ γίνεται η ανάλυση κατηγοριών)
CREATE TABLE Bank_Transaction_Splits (
    Split_Id SERIAL PRIMARY KEY,
    Bank_Transactions_Id INTEGER REFERENCES Bank_Transactions(Bank_Transactions_Id) ON DELETE CASCADE,
    Categories_Id INTEGER REFERENCES Categories(Categories_Id),
    Amount NUMERIC(28, 18),
    Memo TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_split_id ON Bank_Transaction_Splits(Split_Id);

CREATE OR REPLACE FUNCTION update_account_balance()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        UPDATE Accounts SET Account_Balance = Account_Balance + NEW.Total_Amount 
        WHERE Accounts_Id = NEW.Accounts_Id;
    ELSIF (TG_OP = 'DELETE') THEN
        UPDATE Accounts SET Account_Balance = Account_Balance - OLD.Total_Amount 
        WHERE Accounts_Id = OLD.Accounts_Id;
    ELSIF (TG_OP = 'UPDATE') THEN
        -- 1. Αφαιρούμε το παλιό ποσό από τον ΠΑΛΙΟ λογαριασμό
        UPDATE Accounts SET Account_Balance = Account_Balance - OLD.Total_Amount 
        WHERE Accounts_Id = OLD.Accounts_Id;
        -- 2. Προσθέτουμε το νέο ποσό στον ΝΕΟ λογαριασμό
        UPDATE Accounts SET Account_Balance = Account_Balance + NEW.Total_Amount 
        WHERE Accounts_Id = NEW.Accounts_Id;		
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_balance
AFTER INSERT OR UPDATE OR DELETE ON Bank_Transactions
FOR EACH ROW EXECUTE FUNCTION update_account_balance();


CREATE TABLE Investment_Transactions (
    Inv_Transactions_Id SERIAL PRIMARY KEY,
    Accounts_Id INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
    Securities_Id INTEGER REFERENCES Securities(Securities_Id),
    Date DATE NOT NULL,
    Action Investment_Action NOT NULL,
    Quantity NUMERIC(28, 18),         -- Αριθμός μετοχών
    Price_Per_Share NUMERIC(20, 8),
    Commission NUMERIC(20, 8) DEFAULT 0,
    Total_Amount NUMERIC(28, 18),      -- Συνολικό ποσό μετρητών που κινήθηκε
    Description TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inv_transaction_id ON Investment_Transactions(Inv_Transactions_Id);

-- Ιστορικό τιμών για Μετοχές, ETFs, Crypto, PF Units
CREATE TABLE Historical_Prices (
    Securities_Id INTEGER REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
    Price_Date DATE NOT NULL,
    Price_Close NUMERIC(20, 8) NOT NULL,
    Volume BIGINT,
    PRIMARY KEY (Securities_Id, Price_Date)
);
ALTER TABLE Historical_Prices ADD UNIQUE (Securities_Id, Price_Date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_id ON Historical_Prices(Securities_Id, Price_Date);

-- Ιστορικό ισοτιμιών (FX Rates)
CREATE TABLE Historical_FX (
    Base_Currency_Id INTEGER REFERENCES Currencies(Currencies_Id),   -- π.χ. EUR
    Target_Currency_Id INTEGER REFERENCES Currencies(Currencies_Id), -- π.χ. GBP
    FX_Date DATE NOT NULL,
    FX_Rate NUMERIC(18, 10) NOT NULL,
    PRIMARY KEY (Base_Currency_Id, Target_Currency_Id, FX_Date)
);
ALTER TABLE Historical_FX ADD UNIQUE (Base_Currency_Id, Target_Currency_Id, FX_Date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fxrate_id ON Historical_FX(Base_Currency_Id, Target_Currency_Id, FX_Date);
