"""e04s01 — clasificación de fallos (§4.3): T5/T52/T53/T54/T55."""
# story: e04s01

from tikdown_rs.core.download_engine import classify_failure


def test_auth_requiring_login_definitivo():
    """T52: marcador de auth → definitivo."""
    assert classify_failure("TikTok is requiring login for access to this content") == "definitive"


def test_auth_log_into_account_definitivo():
    """T52: 'Log into an account' → definitivo (literal real del extractor)."""
    msg = "You do not have permission to view this post. Log into an account that has access"
    assert classify_failure(msg) == "definitive"


def test_auth_log_in_for_access_definitivo():
    """T52: 'log in for access' → definitivo."""
    assert classify_failure("This post may not be comfortable. Log in for access") == "definitive"


def test_status_code_0_transitorio():
    """T53: 'status code 0' → transitorio (respuesta degradada, no inexistente)."""
    assert classify_failure("Video not available, status code 0") == "transient"


def test_keeps_sending_same_page_transitorio():
    """T54: 'keeps sending the same page' → transitorio."""
    assert classify_failure("TikTok is keeps sending the same page") == "transient"


def test_403_sin_auth_transitorio():
    """T5: 403 sin hints de auth → transitorio (nunca definitivo)."""
    assert classify_failure("HTTP Error 403: Forbidden") == "transient"


def test_404_definitivo():
    """404 → definitivo (contenido inexistente)."""
    assert classify_failure("HTTP Error 404: Not Found") == "definitive"


def test_video_unavailable_definitivo():
    """'video unavailable' → definitivo."""
    assert classify_failure("This video is unavailable") == "definitive"


def test_captcha_definitivo():
    """'captcha' → definitivo."""
    assert classify_failure("Captcha required to continue") == "definitive"
