from flask import Flask, render_template, request
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import ddddocr
import pandas as pd
import re
import os
import time
import logging

app = Flask(__name__)

# 設置日誌
logging.basicConfig(level=logging.DEBUG)

def scrape_data(username, password):
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=chrome_options)

    try:
        url = 'http://hisweb.hosp.ncku/hisservice/common/nckuhisex/pcs/'
        driver.get(url)

        app.logger.info("開始登入操作")

        driver.find_element(By.ID, "TextBoxId").send_keys(username)
        driver.find_element(By.ID, "TextBoxPwd").send_keys(password)

        captcha_element = driver.find_element(By.ID, "imgValg")
        captcha_element.screenshot('captcha.png')
        
        ocr = ddddocr.DdddOcr()
        with open('captcha.png', 'rb') as f:
            captcha_text = ocr.classification(f.read())

        driver.find_element(By.ID, "txtValidator").send_keys(captcha_text)
        driver.find_element(By.ID, "Button1").click()

        app.logger.info("登入操作完成")

        # 登入11A station
        driver.find_element(By.ID, "cboStation").send_keys("11A")

        table = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, '//*[@id="GridView1"]')))

        app.logger.info("找到表格元素")

        headers = [th.text.strip() for th in table.find_elements(By.TAG_NAME, "th")]
        needed_columns = ['床號', '病歷號', '主治醫師', 'Primary Care', '入院日']
        column_indices = [headers.index(col) for col in needed_columns if col in headers]

        rows = []
        for tr in table.find_elements(By.TAG_NAME, "tr")[1:]:
            cells = tr.find_elements(By.TAG_NAME, "td")
            row = [cells[i].text.strip() for i in column_indices]
            rows.append(row)

        app.logger.info(f"提取了 {len(rows)} 行數據")

        df = pd.DataFrame(rows, columns=needed_columns)
        df['床號'] = df['床號'].apply(lambda x: re.sub(r'^11A', '', x))
        df['入院日'] = df['入院日'].str.extract(r'\((\d+)日\)')

        app.logger.info("數據處理完成")

        return df

    except Exception as e:
        app.logger.error(f"發生錯誤: {str(e)}")
        return str(e)

    finally:
        driver.quit()

def create_structured_table(df, main_doctors):
    
    
    main_doctors_html = "<table class='table table-bordered main-table'>"
    other_doctors_html = "<table class='table table-bordered main-table'>"
    
    for doctor in main_doctors:
        doctor_data = df[df['主治醫師'] == doctor]
        main_doctors_html += create_doctor_table(doctor, doctor_data)
    
    other_doctors_data = df[~df['主治醫師'].isin(main_doctors)]
    other_doctors_html += "<tr><th colspan='100'>其他主治醫師</th></tr>"
    for doctor, data in other_doctors_data.groupby('主治醫師'):
        other_doctors_html += create_doctor_table(doctor, data)
    
    no_primary_care = df[df['Primary Care'].isna()]
    if not no_primary_care.empty:
        other_doctors_html += "<tr><th colspan='100'>無 Primary Care</th></tr>"
        for _, row in no_primary_care.iterrows():
            other_doctors_html += f"<tr><td colspan='100'>{row['主治醫師']}: {row['床號']}</td></tr>"
    
    main_doctors_html += "</table>"
    other_doctors_html += "</table>"
    
    return main_doctors_html, other_doctors_html

def create_doctor_table(doctor, data):
    html = f"<tr><th colspan='100'>{doctor}</th></tr>"
    for primary_care, group in data.groupby('Primary Care'):
        html += f"<tr><td>{primary_care}</td>"
        for bed in group['床號']:
            html += f"<td class='bed-cell'>{bed}</td>"
        html += "</tr>"
    return html

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scrape', methods=['POST'])
def scrape():
    username = request.form['username']
    password = request.form['password']
    main_doctors = request.form.getlist('main_doctors')
    app.logger.info(f"開始處理用戶 {username} 的請求")
    result = scrape_data(username, password)
    primary_care_summary = {}
    doctor_summary = {}
    main_doctors_table = ""
    other_doctors_table = ""
    if isinstance(result, pd.DataFrame):
        app.logger.info("成功獲取數據，開始處理數據")
        main_doctors_table, other_doctors_table = create_structured_table(result, main_doctors)
        
        # 清理 Primary Care 欄位，刪除括號及其內容
        result['Primary Care'] = result['Primary Care'].apply(lambda x: re.sub(r'\s*\([^)]*\)', '', str(x)) if pd.notna(x) else x)
        
        # 排除 Primary Care 為 None 或空字符串的記錄
        df_filtered = result[result['Primary Care'].notna() & (result['Primary Care'] != '')]
        
        app.logger.info(f"過濾後的數據行數: {len(df_filtered)}")
        
        # 計算主治醫師病人數量
        doctor_summary = result[result['主治醫師'].isin(main_doctors)]['主治醫師'].value_counts().to_dict()
        app.logger.info(f"主治醫師病人數量: {doctor_summary}")
        
        # 計算 Primary Care 病人數量
        primary_care_summary = df_filtered['Primary Care'].value_counts().to_dict()
        app.logger.info(f"Primary Care 病人數量: {primary_care_summary}")
        
        # 檢查數據
        app.logger.info(f"主治醫師唯一值: {df_filtered['主治醫師'].unique()}")
        app.logger.info(f"Primary Care 唯一值: {df_filtered['Primary Care'].unique()}")
        
        
        app.logger.info("結構化表格創建完成，長度：" + str(len(result)))
        app.logger.info(f"Primary Care Summary before rendering: {primary_care_summary}")
        return render_template('result.html', 
                               main_doctors_table=main_doctors_table,
                               other_doctors_table=other_doctors_table,
                               doctor_summary=doctor_summary,
                               primary_care_summary=primary_care_summary,
                               selected_main_doctors=main_doctors)
    else:
        app.logger.error(f"獲取數據失敗: {result}")
        error_message = f"發生錯誤: {result}"
        return render_template('error.html', error=error_message)



if __name__ == '__main__':
    app.run(debug=True)