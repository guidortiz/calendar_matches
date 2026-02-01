#!/usr/bin/env python3
"""
Generador de calendario ICS para partidos de River Plate, Boca Juniors y Argentina.
Scrapea datos de ESPN Argentina y genera un archivo .ics suscribible.
"""

import os
import sys
import re
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import requests

try:
    from icalendar import Calendar, Event
    from bs4 import BeautifulSoup
    import pytz
except ImportError:
    print("Error: Dependencias no instaladas.")
    print("Ejecuta: pip install icalendar pytz requests beautifulsoup4")
    sys.exit(1)

# Timezone Argentina
TIMEZONE = pytz.timezone("America/Argentina/Buenos_Aires")

# Configuración de equipos - IDs de ESPN
TEAMS = {
    "river": {
        "espn_id": "16",
        "name": "River Plate",
        "espn_name": "river-plate",
        "leagues": {
            "liga": "arg.1",
            "sudamericana": "conmebol.sudamericana"  # River juega Sudamericana 2026
        }
    },
    "boca": {
        "espn_id": "5",
        "name": "Boca Juniors",
        "espn_name": "boca-juniors",
        "leagues": {
            "liga": "arg.1",
            "libertadores": "conmebol.libertadores"  # Boca juega Libertadores 2026
        }
    },
    "argentina": {
        "espn_id": "202",
        "name": "Argentina",
        "espn_name": "argentina",
        "leagues": {
            "mundial": "fifa.world"
        },
        "is_national": True
    }
}

# Competiciones permitidas para clubes
ALLOWED_CLUB_COMPETITIONS = [
    "liga profesional",
    "copa de la liga",
    "copa argentina",
    "copa libertadores",
    "libertadores",
    "copa sudamericana",
    "sudamericana",
    "supercopa",
    "trofeo de campeones",
    "recopa",
]

# Competiciones permitidas para Argentina (Mundial + Amistosos + Finalissima)
ALLOWED_ARGENTINA_COMPETITIONS = [
    "fifa world cup",
    "world cup",
    "mundial",
    "copa del mundo",
    "friendly",
    "amistoso",
    "international friendly",
    "finalissima",
    "conmebol-uefa",
]

# Headers para requests
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def is_allowed_competition(competition: str, team_key: str) -> bool:
    """Verifica si la competición está permitida según el equipo."""
    comp_lower = competition.lower()

    if team_key == "argentina":
        # Solo Mundial para Argentina
        return any(allowed in comp_lower for allowed in ALLOWED_ARGENTINA_COMPETITIONS)
    else:
        # Clubes: liga y copas
        return any(allowed in comp_lower for allowed in ALLOWED_CLUB_COMPETITIONS)


def parse_espn_date(date_str: str, time_str: str, year: int) -> Optional[datetime]:
    """
    Parsea fecha y hora de ESPN al formato datetime.

    Args:
        date_str: Fecha en formato "Dom, 1 Feb" o similar
        time_str: Hora en formato "21:30" o "P.A."
        year: Año actual

    Returns:
        datetime object o None si no se puede parsear
    """
    if not date_str:
        return None

    # Mapeo de meses en español
    months = {
        'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12
    }

    try:
        # Extraer día y mes del string
        # Formato esperado: "Dom, 1 Feb" o "1 Feb"
        parts = date_str.replace(',', '').split()

        day = None
        month = None

        for i, part in enumerate(parts):
            # Buscar número (día)
            if part.isdigit():
                day = int(part)
            # Buscar mes
            part_lower = part.lower()[:3]
            if part_lower in months:
                month = months[part_lower]

        if not day or not month:
            return None

        # Parsear hora
        hour, minute = 0, 0
        if time_str and time_str not in ['P.A.', 'TBD', '-', 'A conf.']:
            # Manejar formato 12h (7:15 PM) o 24h (21:30)
            time_clean = time_str.strip().upper()
            if 'PM' in time_clean or 'AM' in time_clean:
                is_pm = 'PM' in time_clean
                time_clean = time_clean.replace('PM', '').replace('AM', '').strip()
                time_parts = time_clean.replace(':', ' ').split()
                hour = int(time_parts[0])
                minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                if is_pm and hour != 12:
                    hour += 12
                elif not is_pm and hour == 12:
                    hour = 0
            else:
                time_parts = time_str.replace(':', ' ').split()
                hour = int(time_parts[0])
                minute = int(time_parts[1]) if len(time_parts) > 1 else 0

        dt = datetime(year, month, day, hour, minute)
        return TIMEZONE.localize(dt)

    except (ValueError, IndexError) as e:
        print(f"    Warning: No se pudo parsear fecha '{date_str}' '{time_str}': {e}")
        return None


def parse_espn_date_v2(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Parsea fecha y hora de ESPN con formato "Dom., 1 de Feb." y "9:30 PM"
    """
    if not date_str:
        return None

    months = {
        'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12
    }

    try:
        # Extraer día y mes: "Dom., 1 de Feb."
        # Buscar número y mes
        day_match = re.search(r'(\d{1,2})', date_str)
        if not day_match:
            return None
        day = int(day_match.group(1))

        month = None
        date_lower = date_str.lower()
        for month_name, month_num in months.items():
            if month_name in date_lower:
                month = month_num
                break

        if not month:
            return None

        # Año actual o siguiente si el mes ya pasó
        year = datetime.now().year
        current_month = datetime.now().month
        if month < current_month - 1:  # Si el mes es anterior, es del año siguiente
            year += 1

        # Parsear hora: "9:30 PM" o "P.A."
        hour, minute = 0, 0
        if time_str and time_str not in ['P.A.', 'TBD', '-', 'A conf.']:
            time_clean = time_str.strip().upper()
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_clean)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))

                if 'PM' in time_clean and hour != 12:
                    hour += 12
                elif 'AM' in time_clean and hour == 12:
                    hour = 0

        dt = datetime(year, month, day, hour, minute)
        return TIMEZONE.localize(dt)

    except Exception as e:
        return None


def fetch_espn_fixtures(team_key: str) -> List[Dict]:
    """
    Obtiene los fixtures de un equipo desde ESPN Argentina.
    Estructura de celdas: [FECHA, LOCAL, "v", VISITANTE, HORA, COMPETENCIA, TV]
    """
    team = TEAMS[team_key]
    url = f"https://www.espn.com.ar/futbol/equipo/calendario/_/id/{team['espn_id']}/{team['espn_name']}"

    print(f"    Fetching: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        fixtures = []

        # Buscar todas las tablas
        tables = soup.find_all('table')

        for table in tables:
            rows = table.find_all('tr')

            for row in rows:
                cells = row.find_all('td')

                # Necesitamos al menos 6 celdas: FECHA, LOCAL, v, VISITANTE, HORA, COMPETENCIA
                if len(cells) < 6:
                    continue

                try:
                    date_str = cells[0].get_text(strip=True)
                    home_team = cells[1].get_text(strip=True)
                    separator = cells[2].get_text(strip=True)
                    away_team = cells[3].get_text(strip=True)
                    time_str = cells[4].get_text(strip=True)
                    competition = cells[5].get_text(strip=True)

                    # Verificar que es una fila de partido (tiene separador "v")
                    if separator != 'v':
                        continue

                    # Verificar competición permitida
                    if not is_allowed_competition(competition, team_key):
                        continue

                    # Parsear fecha
                    match_date = parse_espn_date_v2(date_str, time_str)
                    if not match_date:
                        continue

                    # Solo partidos futuros
                    now = datetime.now(TIMEZONE)
                    if match_date < now - timedelta(hours=3):  # Margen de 3 horas
                        continue

                    fixture = {
                        "date": match_date.isoformat(),
                        "home_team": home_team,
                        "away_team": away_team,
                        "competition": competition,
                        "venue": "Por confirmar",
                        "team_key": team_key
                    }

                    fixtures.append(fixture)

                except Exception:
                    continue

        return fixtures

    except requests.RequestException as e:
        print(f"    Error fetching ESPN data: {e}")
        return []


def fetch_espn_scoreboard() -> List[Dict]:
    """
    Obtiene partidos del scoreboard de ESPN para Argentina Liga.
    Este endpoint tiene partidos futuros.
    """
    fixtures = []

    # Obtener partidos de las próximas semanas
    base_url = "https://site.api.espn.com/apis/site/v2/sports/soccer/arg.1/scoreboard"

    for days_ahead in range(0, 120, 7):  # Próximos 4 meses
        target_date = datetime.now() + timedelta(days=days_ahead)
        date_str = target_date.strftime("%Y%m%d")

        try:
            url = f"{base_url}?dates={date_str}"
            response = requests.get(url, headers=HEADERS, timeout=15)

            if response.status_code != 200:
                continue

            data = response.json()
            events = data.get('events', [])

            for event in events:
                try:
                    # Parsear fecha
                    event_date = event.get('date', '')
                    if event_date:
                        match_date = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
                        match_date = match_date.astimezone(TIMEZONE)
                    else:
                        continue

                    # Equipos
                    competitors = event.get('competitions', [{}])[0].get('competitors', [])
                    home_team = ""
                    away_team = ""
                    our_team = None

                    for comp in competitors:
                        team_name = comp.get('team', {}).get('displayName', '')
                        team_id = comp.get('team', {}).get('id', '')

                        if comp.get('homeAway') == 'home':
                            home_team = team_name
                        else:
                            away_team = team_name

                        # Verificar si es River o Boca
                        if team_id == '16':
                            our_team = 'river'
                        elif team_id == '5':
                            our_team = 'boca'

                    if not our_team:
                        continue

                    # Competición
                    league = event.get('season', {}).get('type', {}).get('name', '')
                    if not league:
                        league = "Liga Profesional"

                    # Venue
                    venue = event.get('competitions', [{}])[0].get('venue', {}).get('fullName', 'Por confirmar')

                    fixture = {
                        "date": match_date.isoformat(),
                        "home_team": home_team,
                        "away_team": away_team,
                        "competition": league,
                        "venue": venue,
                        "team_key": our_team
                    }

                    fixtures.append(fixture)

                except Exception:
                    continue

        except Exception:
            continue

    return fixtures


def fetch_international_cups(team_key: str) -> List[Dict]:
    """
    Obtiene los partidos de copas internacionales (Libertadores/Sudamericana) desde ESPN.
    """
    team = TEAMS[team_key]
    fixtures = []

    # Obtener las ligas internacionales del equipo
    leagues = team.get("leagues", {})

    for league_name, league_code in leagues.items():
        # Saltar liga argentina (ya se obtiene por separado)
        if league_code == "arg.1":
            continue

        url = f"https://www.espn.com/soccer/team/fixtures/_/id/{team['espn_id']}/league/{league_code}/{team['espn_name']}"
        print(f"    Fetching {league_name}: {url}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Buscar todas las tablas
            tables = soup.find_all('table')

            for table in tables:
                rows = table.find_all('tr')

                for row in rows:
                    cells = row.find_all('td')

                    if len(cells) < 5:
                        continue

                    try:
                        date_str = cells[0].get_text(strip=True)
                        home_team = cells[1].get_text(strip=True)
                        separator = cells[2].get_text(strip=True)
                        away_team = cells[3].get_text(strip=True)
                        time_str = cells[4].get_text(strip=True)

                        if separator != 'v':
                            continue

                        match_date = parse_espn_date_v2(date_str, time_str)
                        if not match_date:
                            continue

                        # Determinar nombre de competición
                        if "libertadores" in league_code.lower():
                            competition = "Copa Libertadores 2026"
                        elif "sudamericana" in league_code.lower():
                            competition = "Copa Sudamericana 2026"
                        else:
                            competition = league_name

                        fixture = {
                            "date": match_date.isoformat(),
                            "home_team": home_team,
                            "away_team": away_team,
                            "competition": competition,
                            "venue": "Por confirmar",
                            "team_key": team_key
                        }

                        fixtures.append(fixture)

                    except Exception:
                        continue

        except requests.RequestException as e:
            print(f"      No hay datos disponibles aún")
            continue

    return fixtures


def fetch_argentina_fixtures() -> List[Dict]:
    """
    Obtiene TODOS los partidos de Argentina desde ESPN.
    Incluye: Mundial, Finalissima, Amistosos
    URL: https://www.espn.com/soccer/team/fixtures/_/id/202/argentina
    """
    team = TEAMS["argentina"]
    # URL sin filtro de liga para obtener TODOS los partidos
    url = f"https://www.espn.com/soccer/team/fixtures/_/id/{team['espn_id']}/{team['espn_name']}"

    print(f"    Fetching: {url}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        fixtures = []

        # Buscar todas las tablas
        tables = soup.find_all('table')

        for table in tables:
            rows = table.find_all('tr')

            for row in rows:
                cells = row.find_all('td')

                # Necesitamos al menos 5 celdas
                if len(cells) < 5:
                    continue

                try:
                    date_str = cells[0].get_text(strip=True)
                    home_team = cells[1].get_text(strip=True)
                    separator = cells[2].get_text(strip=True)
                    away_team = cells[3].get_text(strip=True)
                    time_str = cells[4].get_text(strip=True)

                    # Verificar que es una fila de partido
                    if separator != 'v':
                        continue

                    # Parsear fecha
                    match_date = parse_espn_date_v2(date_str, time_str)
                    if not match_date:
                        continue

                    # Obtener competición si está disponible
                    competition = "Argentina"
                    if len(cells) > 5:
                        comp_text = cells[5].get_text(strip=True)
                        if comp_text:
                            competition = comp_text

                    fixture = {
                        "date": match_date.isoformat(),
                        "home_team": home_team,
                        "away_team": away_team,
                        "competition": competition,
                        "venue": "Por confirmar",
                        "team_key": "argentina"
                    }

                    fixtures.append(fixture)

                except Exception:
                    continue

        return fixtures

    except requests.RequestException as e:
        print(f"    Error fetching ESPN data: {e}")
        return []


def get_finalissima_fixture() -> List[Dict]:
    """
    Retorna el partido de la Finalissima 2026.
    Argentina vs España - 27 de marzo 2026 - Estadio Lusail, Qatar
    """
    return [{
        "date": "2026-03-27T15:00:00-03:00",
        "home_team": "Argentina",
        "away_team": "Espana",
        "competition": "Finalissima 2026 (CONMEBOL-UEFA)",
        "venue": "Estadio Lusail, Qatar",
        "team_key": "argentina"
    }]


def create_event(fixture: Dict) -> Event:
    """Crea un evento de calendario a partir de un fixture."""
    event = Event()

    # Título
    title = f"{fixture['home_team']} vs {fixture['away_team']}"
    if fixture.get('competition'):
        title += f" ({fixture['competition']})"

    # Fecha - convertir a UTC para máxima compatibilidad con Outlook
    try:
        if '+' in fixture['date'] or 'Z' in fixture['date']:
            match_date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
        else:
            match_date = datetime.fromisoformat(fixture['date'])
            if match_date.tzinfo is None:
                match_date = TIMEZONE.localize(match_date)

        # Convertir a UTC para compatibilidad con Outlook
        match_date_utc = match_date.astimezone(pytz.UTC)
    except:
        match_date_utc = datetime.now(pytz.UTC)

    # UID único
    uid_base = f"{fixture['home_team']}-{fixture['away_team']}-{fixture['date']}"
    uid = f"{abs(hash(uid_base))}@futbol-calendar.github.io"

    # Descripción
    description = f"""Competencia: {fixture.get('competition', 'N/A')}
Local: {fixture['home_team']}
Visitante: {fixture['away_team']}
Estadio: {fixture.get('venue', 'Por confirmar')}

Calendario generado automaticamente"""

    event.add("summary", title)
    event.add("dtstart", match_date_utc)
    event.add("dtend", match_date_utc + timedelta(hours=2))
    event.add("location", fixture.get('venue', 'Por confirmar'))
    event.add("description", description)
    event.add("uid", uid)
    event.add("dtstamp", datetime.now(pytz.UTC))

    return event


def create_calendar(all_fixtures: List[Dict]) -> Calendar:
    """Crea el calendario con todos los fixtures."""
    cal = Calendar()

    cal.add("prodid", "-//Futbol Argentina Calendar//github.io//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", "Futbol Argentina - River, Boca y Seleccion")
    cal.add("x-wr-timezone", "America/Argentina/Buenos_Aires")

    # Evitar duplicados
    added = set()

    for fixture in all_fixtures:
        key = f"{fixture['home_team']}-{fixture['away_team']}-{fixture['date']}"
        if key in added:
            continue
        added.add(key)

        event = create_event(fixture)
        cal.add_component(event)

    return cal


def main():
    """Función principal."""
    print("=" * 60)
    print("Generador de Calendario - Futbol Argentina")
    print("Fuente: ESPN Argentina")
    print("=" * 60)

    all_fixtures = []

    # Obtener fixtures del scoreboard (partidos programados)
    print("\nBuscando partidos en ESPN...")
    scoreboard_fixtures = fetch_espn_scoreboard()

    # Separar por equipo
    river_fixtures = [f for f in scoreboard_fixtures if f['team_key'] == 'river']
    boca_fixtures = [f for f in scoreboard_fixtures if f['team_key'] == 'boca']

    print(f"  River Plate: {len(river_fixtures)} partidos")
    print(f"  Boca Juniors: {len(boca_fixtures)} partidos")

    all_fixtures.extend(scoreboard_fixtures)

    # Si no encontramos partidos del scoreboard, intentar scraping
    if not all_fixtures:
        print("\n  Intentando scraping directo...")
        for team_key in ["river", "boca"]:
            team_name = TEAMS[team_key]["name"]
            fixtures = fetch_espn_fixtures(team_key)
            print(f"  {team_name}: {len(fixtures)} partidos")
            all_fixtures.extend(fixtures)

    # Obtener partidos de copas internacionales
    print("\nBuscando partidos de copas internacionales...")
    for team_key in ["river", "boca"]:
        team_name = TEAMS[team_key]["name"]
        print(f"  {team_name}:")
        cup_fixtures = fetch_international_cups(team_key)
        if cup_fixtures:
            print(f"    Encontrados: {len(cup_fixtures)} partidos")
            all_fixtures.extend(cup_fixtures)
        else:
            print(f"    Sin fixtures (sorteo pendiente)")

    # Obtener partidos de Argentina (Mundial + Amistosos)
    print(f"\nBuscando partidos de Argentina...")
    argentina_fixtures = fetch_argentina_fixtures()
    print(f"  ESPN: {len(argentina_fixtures)} partidos")
    all_fixtures.extend(argentina_fixtures)

    # Agregar Finalissima (no siempre aparece en ESPN)
    finalissima = get_finalissima_fixture()
    print(f"  Finalissima: {len(finalissima)} partido")
    all_fixtures.extend(finalissima)

    # Ordenar por fecha
    all_fixtures.sort(key=lambda x: x.get('date', ''))

    # Generar calendario
    print("\nGenerando archivo ICS...")
    calendar = create_calendar(all_fixtures)

    # Guardar
    output_file = "futbol-argentina.ics"
    with open(output_file, "wb") as f:
        f.write(calendar.to_ical())

    print(f"\nArchivo generado: {output_file}")
    print(f"Total de eventos: {len(all_fixtures)}")

    # Mostrar próximos partidos
    print("\nPróximos partidos:")
    for fixture in all_fixtures[:10]:
        try:
            date = datetime.fromisoformat(fixture['date'].replace('Z', '+00:00'))
            date_str = date.strftime("%d/%m %H:%M")
        except:
            date_str = "TBD"
        print(f"  {date_str} - {fixture['home_team']} vs {fixture['away_team']} ({fixture['competition']})")

    print("\n" + "=" * 60)
    print("Proceso completado!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
