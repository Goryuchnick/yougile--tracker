import requests
import json
import os

LOGIN = os.environ.get("YOUGILE_LOGIN", "")
PASSWORD = os.environ.get("YOUGILE_PASSWORD", "")
BASE_URL = "https://yougile.com/api-v2"

def main():
    print(f"Попытка авторизации с логином: {LOGIN}")
    
    # 1. Получаем список компаний
    companies_url = f"{BASE_URL}/auth/companies"
    auth_data = {
        "login": LOGIN,
        "password": PASSWORD
    }
    
    try:
        print("Запрос списка компаний...")
        resp = requests.post(companies_url, json=auth_data)
        
        if resp.status_code != 200:
            print(f"Ошибка получения списка компаний. Код: {resp.status_code}")
            print(resp.text)
            return
            
        data = resp.json()
        companies = data.get('content', [])
        
        if not companies:
            print("У пользователя нет доступных компаний.")
            return
            
        print(f"Найдено компаний: {len(companies)}")
        company = companies[0] # Берем первую по умолчанию
        company_id = company['id']
        company_name = company['name']
        
        print(f"Используем компанию: {company_name} (ID: {company_id})")
        
        # 2. Создаем/Получаем API ключ
        keys_url = f"{BASE_URL}/auth/keys"
        key_data = {
            "login": LOGIN,
            "password": PASSWORD,
            "companyId": company_id
        }
        
        print("Запрос API ключа...")
        key_resp = requests.post(keys_url, json=key_data)
        
        if key_resp.status_code == 201:
            api_key = key_resp.json().get('key')
            print("\n" + "="*40)
            print(f"ВАШ API КЛЮЧ: {api_key}")
            print("="*40 + "\n")
            
            # Сохраняем ключ в файл для удобства
            with open("yougile_key.txt", "w") as f:
                f.write(api_key)
                print("Ключ сохранен в файл yougile_key.txt")
                
        elif key_resp.status_code == 400: # Возможно ключ уже создан или лимит
             print("Не удалось создать новый ключ. Пробуем получить список существующих...")
             
             get_keys_url = f"{BASE_URL}/auth/keys/get"
             # Для получения списка ключей тоже нужен companyId в теле запроса (согласно документации)
             get_keys_data = {
                 "login": LOGIN,
                 "password": PASSWORD,
                 "companyId": company_id
             }
             
             list_resp = requests.post(get_keys_url, json=get_keys_data)
             
             if list_resp.status_code == 200:
                 keys_list = list_resp.json()
                 if keys_list:
                     api_key = keys_list[0]['key']
                     print("\n" + "="*40)
                     print(f"СУЩЕСТВУЮЩИЙ API КЛЮЧ: {api_key}")
                     print("="*40 + "\n")
                     with open("yougile_key.txt", "w") as f:
                        f.write(api_key)
                 else:
                     print("Список ключей пуст, но создать новый не удалось.")
             else:
                 print(f"Ошибка получения списка ключей: {list_resp.status_code}")
                 print(list_resp.text)

        else:
            print(f"Ошибка создания ключа. Код: {key_resp.status_code}")
            print(key_resp.text)

    except Exception as e:
        print(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
