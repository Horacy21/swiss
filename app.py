#!/usr/bin/env python3
"""
BBP Pairings - FastAPI Tournament Pairing System
Converted from command line to REST API
"""

import json
from typing import List, Dict, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

# Response models
class PairingResponse(BaseModel):
    white: str
    black: str
    is_bye: bool = False

class PairingsResult(BaseModel):
    pairings: List[PairingResponse]
    total_pairings: int
    round_number: int
    system: str

class TournamentInfo(BaseModel):
    total_players: int
    current_round: int
    system: str
    players: List[Dict]

# Input models
class PlayerInput(BaseModel):
    id: str
    rating: int
    score: float
    color_history: List[str] = []
    opponents: List[str] = []
    has_bye: bool = False

class TournamentInput(BaseModel):
    players: List[PlayerInput]
    system: str = "dutch"

class Color(Enum):
    NONE = 0
    WHITE = 1
    BLACK = 2

class SwissSystem(Enum):
    DUTCH = "dutch"
    BURSTEIN = "burstein"

@dataclass
class Player:
    id: str
    rating: int
    score: float
    color_history: List[str]
    opponents: List[str]
    rank: int = 0
    color_balance: int = 0  # positive = more whites, negative = more blacks
    preferred_color: Color = Color.NONE
    has_bye: bool = False  # Track if player has received a bye
    
    def __post_init__(self):
        self.calculate_color_balance()
        self.determine_preferred_color()
    
    def calculate_color_balance(self):
        """Calculate color balance from history"""
        whites = self.color_history.count("white")
        blacks = self.color_history.count("black")
        self.color_balance = whites - blacks
    
    def determine_preferred_color(self):
        """Determine preferred color based on balance"""
        if self.color_balance > 0:
            self.preferred_color = Color.BLACK
        elif self.color_balance < 0:
            self.preferred_color = Color.WHITE
        else:
            self.preferred_color = Color.NONE

@dataclass
class Pairing:
    white: str
    black: str
    is_bye: bool = False
    
    def __init__(self, white: str, black: str = ""):
        self.white = white
        self.black = black
        self.is_bye = black == "" or black == "0"

class Tournament:
    def __init__(self, json_data: Dict):
        self.players: List[Player] = []
        self.current_round = 1
        self.load_from_json(json_data)
        self.update_ranks()
    
    def load_from_json(self, json_data: Dict):
        """Load tournament data from JSON"""
        if "players" not in json_data:
            raise ValueError("JSON must contain 'players' field")
        
        for player_data in json_data["players"]:
            player = Player(
                id=str(player_data["id"]),
                rating=int(player_data["rating"]),
                score=float(player_data["score"]),
                color_history=player_data.get("color_history", []),
                opponents=player_data.get("opponents", []),
                has_bye=player_data.get("has_bye", False)
            )
            self.players.append(player)
        
        # Determine current round from color history
        if self.players:
            # Find the maximum games played to determine round
            max_games = max(len(p.color_history) for p in self.players) if self.players else 0
            self.current_round = max_games + 1
    
    def update_ranks(self):
        """Update player rankings based on score and rating"""
        # Sort by score (descending), then by rating (descending)
        self.players.sort(key=lambda p: (-p.score, -p.rating))
        
        # Assign ranks
        for i, player in enumerate(self.players):
            player.rank = i + 1

class SwissPairingEngine:
    def __init__(self, swiss_system: SwissSystem = SwissSystem.DUTCH):
        self.swiss_system = swiss_system
    
    def compute_pairings(self, tournament: Tournament) -> List[Pairing]:
        """Compute next round pairings"""
        available_players = [p for p in tournament.players]
        pairings = []
        
        # Handle late joiners - players with fewer games than current round - 1
        max_games = max(len(p.color_history) for p in available_players) if available_players else 0
        
        # Give byes to late joiners who need to catch up
        late_joiners = [p for p in available_players if len(p.color_history) < max_games]
        for late_joiner in late_joiners:
            available_players.remove(late_joiner)
            late_joiner.has_bye = True
            pairings.append(Pairing(late_joiner.id))
        
        # Handle odd number of remaining players (bye)
        if len(available_players) % 2 == 1:
            bye_player = self.select_bye_player(available_players)
            available_players.remove(bye_player)
            bye_player.has_bye = True
            pairings.append(Pairing(bye_player.id))
        
        # Create pairings using Swiss system
        while len(available_players) >= 2:
            player1 = available_players.pop(0)
            player2 = self.find_best_opponent(player1, available_players)
            
            if player2 is None:
                raise Exception(f"No valid opponent found for player {player1.id}")
            
            available_players.remove(player2)
            
            # Determine colors
            white_player, black_player = self.assign_colors(player1, player2)
            pairings.append(Pairing(white_player.id, black_player.id))
        
        return pairings
    
    def select_bye_player(self, players: List[Player]) -> Player:
        """Select player for bye - lowest ranked player who hasn't had a bye yet"""
        # Filter players who haven't had a bye
        no_bye_players = [p for p in players if not p.has_bye]
        
        if no_bye_players:
            # Select lowest ranked player (highest index after sorting) who hasn't had a bye
            return max(no_bye_players, key=lambda p: p.rank)
        else:
            # If all players have had a bye, select the lowest ranked overall
            return max(players, key=lambda p: p.rank)
    
    def find_best_opponent(self, player: Player, candidates: List[Player]) -> Optional[Player]:
        """Find best opponent for given player"""
        # Filter out players already played against
        valid_opponents = [p for p in candidates if p.id not in player.opponents]
        
        if not valid_opponents:
            # If no new opponents, allow rematches (shouldn't happen in well-formed tournaments)
            valid_opponents = candidates
        
        if not valid_opponents:
            return None
        
        # Prefer opponents with similar scores
        valid_opponents.sort(key=lambda p: abs(p.score - player.score))
        return valid_opponents[0]
    
    def assign_colors(self, player1: Player, player2: Player) -> Tuple[Player, Player]:
        """Assign colors based on preferences and balance"""
        # Strong preference based on color balance
        if player1.color_balance > player2.color_balance:
            return player2, player1  # player1 gets black
        elif player2.color_balance > player1.color_balance:
            return player1, player2  # player2 gets black
        
        # If balanced, higher rated player gets white
        if player1.rating >= player2.rating:
            return player1, player2
        else:
            return player2, player1

# FastAPI app
app = FastAPI(
    title="Swiss Tournament Pairing System",
    description="BBP Pairings - JSON-based Tournament Pairing System",
    version="1.0.0"
)

@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "BBP Pairings FastAPI Server",
        "version": "1.0.0",
        "built": datetime.now().strftime('%b %d %Y %H:%M:%S'),
        "endpoints": {
            "POST /pairings": "Generate next round pairings",
            "POST /check": "Check tournament data validity",
            "GET /info": "Get API information"
        }
    }

@app.get("/info")
async def get_info():
    """Get API information"""
    return {
        "name": "BBP Pairings",
        "description": "Swiss Tournament Pairing System",
        "version": "1.0.0",
        "built": datetime.now().strftime('%b %d %Y %H:%M:%S'),
        "supported_systems": ["dutch", "burstein"]
    }

@app.post("/pairings", response_model=PairingsResult)
async def generate_pairings(tournament_data: TournamentInput):
    """Generate next round pairings"""
    try:
        # Convert Pydantic model to dict
        json_data = tournament_data.dict()
        
        # Determine Swiss system
        swiss_system = SwissSystem.DUTCH
        if tournament_data.system.lower() == "burstein":
            swiss_system = SwissSystem.BURSTEIN
        
        # Create tournament and engine
        tournament = Tournament(json_data)
        engine = SwissPairingEngine(swiss_system)
        
        # Generate pairings
        pairings = engine.compute_pairings(tournament)
        
        # Convert to response format
        pairing_responses = []
        for pairing in pairings:
            pairing_responses.append(PairingResponse(
                white=pairing.white,
                black=pairing.black if not pairing.is_bye else "",
                is_bye=pairing.is_bye
            ))
        
        return PairingsResult(
            pairings=pairing_responses,
            total_pairings=len(pairing_responses),
            round_number=tournament.current_round,
            system=tournament_data.system
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@app.post("/add_player")
async def add_player(player_data: PlayerInput, current_round: int = 1):
    """Add a new player to the tournament (allowed only before round 2)"""
    try:
        if current_round > 2:
            raise HTTPException(
                status_code=400, 
                detail="Cannot add players after round 2 has started"
            )
        
        # If joining after round 1 has started, give them a bye for round 1
        if current_round > 1:
            player_data.has_bye = True
            # Add a bye to their history for the missed round(s)
            missed_rounds = current_round - 1
            for _ in range(missed_rounds):
                player_data.score += 0.5  # Half point for bye
        
        return {
            "message": f"Player {player_data.id} added successfully",
            "late_joiner": current_round > 1,
            "bye_awarded": current_round > 1
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding player: {str(e)}")

@app.post("/check", response_model=TournamentInfo)
async def check_tournament(tournament_data: TournamentInput):
    """Check tournament data for validity"""
    try:
        # Convert Pydantic model to dict
        json_data = tournament_data.dict()
        
        # Create tournament
        tournament = Tournament(json_data)
        
        # Basic validation
        warnings = []
        for player in tournament.players:
            if len(player.color_history) != len(player.opponents):
                warnings.append(f"Player {player.id} has mismatched history lengths")
        
        # Convert players to dict format
        players_info = []
        for player in tournament.players:
            players_info.append({
                "id": player.id,
                "rating": player.rating,
                "score": player.score,
                "rank": player.rank,
                "color_balance": player.color_balance,
                "games_played": len(player.color_history),
                "warnings": [w for w in warnings if player.id in w]
            })
        
        return TournamentInfo(
            total_players=len(tournament.players),
            current_round=tournament.current_round,
            system=tournament_data.system,
            players=players_info
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

if __name__ == "__main__":
    uvicorn.run("pairing_api:app", host="0.0.0.0", port=8000, reload=True)