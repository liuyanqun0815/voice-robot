from dataclasses import dataclass


@dataclass
class VoiceSessionDTO:
    session_id: str
    user_id: str


class VoiceRepository:
    def create_session(self, session_id: str, user_id: str) -> VoiceSessionDTO:
        return VoiceSessionDTO(session_id=session_id, user_id=user_id)
