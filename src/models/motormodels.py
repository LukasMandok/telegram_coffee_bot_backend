from motormongo import Document, EmbeddedDocument
from motormongo import BooleanField, BinaryField, StringField, IntegerField, DateTimeField, EmbeddedDocumentField

from ..common.helpers import hash_password, check_password

#---------------------------
# *      Users
#---------------------------

class BaseUserDocument(Document):
    first_name = StringField(required = True)
    last_name  = StringField(required = False)
    
    
class TelegramUserDocument(BaseUserDocument):
    id         = IntegerField(required = True, unique = True)
    username   = StringField(required = False, unique = True)
    last_login = DateTimeField()
    phone      = StringField(required = False, unique = True)
    photo_id   = IntegerField()
    lang_code  = StringField(default = "en")
    
    class Meta:
        collection = 'users'
        created_at_timestamp = True
        updated_at_timestamp = True
        
class FullUserDocument(TelegramUserDocument):
    gsheet_name = StringField(required = True, unique = True)
    is_admin    = BooleanField(default = False)
    
#---------------------------
# *      Configuration
#---------------------------
    
class BasePasswordDocument(EmbeddedDocument):
    hash_value = BinaryField(hash_function = hash_password)
    
    def verify_password(self, password: str) -> bool:
        return check_password(password, self.hash_value)
    
    
class ConfigDocument(Document):
    
    password = EmbeddedDocumentField(document_type = BasePasswordDocument)
    admin    = StringField() #EmbeddedDocumentField(document_type = FullUserDocument)
    
    