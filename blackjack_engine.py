import random

SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']

def create_deck():
    return [f"{rank}{suit}" for suit in SUITS for rank in RANKS]

def card_value(card):
    rank = card[:-1]
    if rank in ['J', 'Q', 'K']:
        return 10
    elif rank == 'A':
        return 11
    else:
        return int(rank)

def hand_value(hand):
    total = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[:-1] == 'A')
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

class BlackjackGame:
    def __init__(self):
        self.deck = create_deck()
        random.shuffle(self.deck)
        self.player_hand = [self.deck.pop(), self.deck.pop()]
        self.dealer_hand = [self.deck.pop(), self.deck.pop()]
        self.finished = False
        self.result = None

    def hit(self):
        if self.finished:
            return
        self.player_hand.append(self.deck.pop())
        if hand_value(self.player_hand) > 21:
            self.finished = True
            self.result = 'player_bust'

    def stand(self):
        if self.finished:
            return
        while hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())
        self.finished = True
        p_val = hand_value(self.player_hand)
        d_val = hand_value(self.dealer_hand)
        if d_val > 21 or p_val > d_val:
            self.result = 'player_win'
        elif p_val < d_val:
            self.result = 'dealer_win'
        else:
            self.result = 'push'

    def state(self):
        return {
            'player_hand': self.player_hand,
            'dealer_hand': self.dealer_hand if self.finished else [self.dealer_hand[0], '??'],
            'player_value': hand_value(self.player_hand),
            'dealer_value': hand_value(self.dealer_hand) if self.finished else '?',
            'finished': self.finished,
            'result': self.result
        }
