"""Custom exceptions for coffee-related operations."""


class CoffeeValidationError(ValueError):
    """Base class for coffee validation errors."""
    pass


class InvalidCoffeeCountError(CoffeeValidationError):
    """Raised when coffee counts have invalid structure (e.g., negative values)."""
    
    def __init__(self, message: str = "Invalid coffee count structure"):
        super().__init__(message)


class InsufficientCoffeeError(CoffeeValidationError):
    """Raised when there aren't enough coffees available for the requested operation."""
    
    def __init__(self, requested: int, available: int, message: str = None):
        self.requested = requested
        self.available = available
        self.shortage = requested - available
        
        if message is None:
            message = (
                f"Insufficient coffee capacity: Requested {requested} coffees, "
                f"but only {available} available (shortage: {self.shortage}). "
                f"Please add more coffee cards to the session."
            )
        
        super().__init__(message)


class SessionNotActiveError(CoffeeValidationError):
    """Raised when trying to perform operations on an inactive session."""
    
    def __init__(self, message: str = "Coffee session is not active"):
        super().__init__(message)


class CoffeeCardError(ValueError):
    """Base class for coffee card related errors."""
    pass


class CoffeeCardNotFoundError(CoffeeCardError):
    """Raised when a coffee card cannot be found."""
    
    def __init__(self, card_id: str = None, message: str = None):
        self.card_id = card_id
        
        if message is None:
            if card_id:
                message = f"Coffee card with ID '{card_id}' not found"
            else:
                message = "Coffee card not found"
                
        super().__init__(message)


class InsufficientCoffeeCardCapacityError(CoffeeCardError):
    """Raised when a coffee card doesn't have enough remaining coffees."""
    
    def __init__(self, requested: int, available: int, card_name: str = None):
        self.requested = requested
        self.available = available
        self.card_name = card_name
        
        if card_name:
            message = f"Card '{card_name}' has insufficient coffees: requested {requested}, available {available}"
        else:
            message = f"Insufficient coffees on card: requested {requested}, available {available}"
            
        super().__init__(message)


class UserNotFoundError(ValueError):
    """Raised when a user cannot be found."""
    
    def __init__(self, user_id: int = None, username: str = None, message: str = None):
        self.user_id = user_id
        self.username = username
        
        if message is None:
            if user_id:
                message = f"User with ID {user_id} not found"
            elif username:
                message = f"User '{username}' not found"
            else:
                message = "User not found"
                
        super().__init__(message)
