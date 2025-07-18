# ClockifyApp

### 1 Datenanbindung

#### 1.1 Clockify Doku verstehen
* API Docu durchlesen
* Relevanten Endunkte dokumentieren und niederschreiben

#### 1.2 Daten manuell mit requests holen
* Skript erstellen, welches Stundenbuchungen über die API holt
* Hardcoded Filter kann man Kunden und Zeitraum angeben, welche die Abfrage filtert
* Json in tabellarischer Form (pandas Dataframe) abspeichern

### 2 Datenaufbereitung

#### 2.1 HTML/CSS File erstellen
* Grundlegende Struktur des PDFs (Demobericht) mit HTML nachbauen
* Tabelle (pandas Dataframe) in HTML einbinden und grafisch aufbereiten (ähnlich wie Demobericht)
* HTML als PDF abspeichern

### 3 Backend aufbauen

#### 3.1 Flask Umgebung aufsetzen
* Endpunkt "Generator": Seite wo Filter gesetzen und Export heruntergeladen werden kann
* Endpunkt "Login": Login Logik aufbauen (User und Passwort checken)
* Endpunkt "Home": Übersicht zur Navigation zu Generator und Login Unterseite

#### 3.2 Dynamische Elemente mit javascript
* Buttons, Filter und Dropdown mit Javascript definieren 
* Downloadbutton aufsetzen

### 4 Frontend aufbauen

* HTML Seiten pro Endpunkt erstellen
* HTML Seiten in Endpunkte einbetten (im backend)


