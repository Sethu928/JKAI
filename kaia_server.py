def get_response(self, user_message):
    if 'je suis heureux' in user_message:
        return "C'est super, je suis content pour toi !"
    elif 'je suis triste' in user_message:
        return "Désolé à entendre cela. Veux-tu parler de ce qui te dérange ?"