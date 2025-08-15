import os
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3
from langgraph.graph import Graph, END
from langgraph.prebuilt import ToolExecutor
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.tools import BaseTool
from langchain.schema import AgentAction, AgentFinish
from langchain.prompts import PromptTemplate
from pydantic import BaseModel, Field
import requests
import chromadb
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State Management
@dataclass
class AgentState:
    """State shared across all agents"""
    user_query: str = ""
    decomposed_query: Dict[str, Any] = None
    tool_calls: List[Dict[str, Any]] = None
    main_agent_response: str = ""
    error_check_results: Dict[str, Any] = None
    final_answer: str = ""
    confidence_score: float = 0.0
    sources: List[str] = None
    errors: List[str] = None

# Database Setup
class DatabaseManager:
    def __init__(self):
        self.setup_databases()
        self.setup_chroma()
    
    def setup_databases(self):
        """Initialize SQLite databases"""
        # Knowledge database for datasets and PDFs
        self.knowledge_conn = sqlite3.connect('agricultural_knowledge.db', check_same_thread=False)
        self.knowledge_cursor = self.knowledge_conn.cursor()
        
        # Metrics database for feedback and success tracking
        self.metrics_conn = sqlite3.connect('metrics_feedback.db', check_same_thread=False)
        self.metrics_cursor = self.metrics_conn.cursor()
        
        # Create tables
        self.knowledge_cursor.execute('''
            CREATE TABLE IF NOT EXISTS crop_data (
                id INTEGER PRIMARY KEY,
                crop_name TEXT,
                season TEXT,
                soil_type TEXT,
                water_requirements TEXT,
                fertilizer_needs TEXT,
                pest_diseases TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.metrics_cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY,
                query TEXT,
                response TEXT,
                rating INTEGER,
                feedback_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.knowledge_conn.commit()
        self.metrics_conn.commit()
    
    def setup_chroma(self):
        """Initialize ChromaDB for RAG"""
        self.chroma_client = chromadb.Client()
        try:
            self.crop_collection = self.chroma_client.get_collection("crop_knowledge")
        except:
            self.crop_collection = self.chroma_client.create_collection("crop_knowledge")

# Tools Definition
class WeatherTool(BaseTool):
    name = "weather_api"
    description = "Get current weather and forecast data for agricultural planning"
    
    def _run(self, location: str, days: int = 7) -> Dict[str, Any]:
        """Mock weather API call - replace with actual API"""
        # In production, use OpenWeatherMap or similar service
        mock_weather = {
            "location": location,
            "current": {
                "temperature": 25,
                "humidity": 65,
                "rainfall": 0,
                "wind_speed": 10
            },
            "forecast": [
                {"day": i, "temp_high": 28, "temp_low": 18, "rainfall_prob": 30}
                for i in range(1, days + 1)
            ]
        }
        logger.info(f"Weather data retrieved for {location}")
        return mock_weather

class CropDatabaseTool(BaseTool):
    name = "crop_database"
    description = "Query crop-specific information from knowledge base"
    
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
    
    def _run(self, crop_name: str, query_type: str = "general") -> Dict[str, Any]:
        """Query crop database"""
        cursor = self.db_manager.knowledge_cursor
        cursor.execute(
            "SELECT * FROM crop_data WHERE crop_name LIKE ?",
            (f"%{crop_name}%",)
        )
        results = cursor.fetchall()
        
        if not results:
            # Query ChromaDB for RAG
            rag_results = self.db_manager.crop_collection.query(
                query_texts=[f"{crop_name} {query_type}"],
                n_results=3
            )
            return {"source": "rag", "data": rag_results}
        
        return {"source": "database", "data": results}

class MarketPriceTool(BaseTool):
    name = "market_prices"
    description = "Get current market prices and trends for crops"
    
    def _run(self, crop: str, region: str = "national") -> Dict[str, Any]:
        """Mock market price API - replace with actual service"""
        mock_prices = {
            "crop": crop,
            "region": region,
            "current_price": 150.0,
            "price_trend": "increasing",
            "demand": "high",
            "supply": "moderate",
            "price_history": [145, 148, 152, 150]
        }
        logger.info(f"Market data retrieved for {crop} in {region}")
        return mock_prices

class SoilAnalysisTool(BaseTool):
    name = "soil_analysis"
    description = "Analyze soil conditions and provide recommendations"
    
    def _run(self, location: str, soil_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Mock soil analysis - integrate with IoT sensors or lab data"""
        mock_analysis = {
            "location": location,
            "ph_level": 6.5,
            "nitrogen": "moderate",
            "phosphorus": "low",
            "potassium": "high",
            "organic_matter": "good",
            "recommendations": [
                "Add phosphorus-rich fertilizer",
                "Maintain current organic matter levels"
            ]
        }
        return mock_analysis

# Agent Implementations
class PlannerAgent:
    def __init__(self, llm):
        self.llm = llm
        self.prompt = PromptTemplate(
            input_variables=["user_query"],
            template="""
            You are an agricultural planning expert. Analyze the user's query and break it down into actionable components.
            
            User Query: {user_query}
            
            Decompose this query into:
            1. Primary intent (what the user wants to achieve)
            2. Required information (what data is needed)
            3. Tools needed (weather, crop data, market prices, soil analysis)
            4. Geographic context (location/region)
            5. Time sensitivity (immediate, seasonal, long-term)
            
            Return a structured JSON response with clear action items.
            """
        )
    
    def execute(self, state: AgentState) -> AgentState:
        """Plan and decompose the user query"""
        logger.info("Planner Agent executing...")
        
        prompt_text = self.prompt.format(user_query=state.user_query)
        response = self.llm.invoke(prompt_text)
        
        try:
            # Extract structured plan from LLM response
            decomposed = {
                "primary_intent": "crop_recommendation",  # Simplified for demo
                "required_info": ["weather", "soil", "market"],
                "tools_needed": ["weather_api", "crop_database", "market_prices"],
                "location": "extracted_location",
                "urgency": "moderate"
            }
            state.decomposed_query = decomposed
            logger.info("Query successfully decomposed")
        except Exception as e:
            logger.error(f"Planning error: {e}")
            state.errors = state.errors or []
            state.errors.append(f"Planning error: {e}")
        
        return state

class MainAgent:
    def __init__(self, llm, tools, db_manager):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.tool_executor = ToolExecutor(tools)
        self.db_manager = db_manager
        
        self.prompt = PromptTemplate(
            input_variables=["user_query", "plan", "tool_results"],
            template="""
            You are an expert agricultural advisor with access to multiple tools and databases.
            
            User Query: {user_query}
            Planning Analysis: {plan}
            Available Tools: weather_api, crop_database, market_prices, soil_analysis
            
            Tool Results: {tool_results}
            
            Based on the planning analysis and tool results, provide comprehensive agricultural advice.
            Consider all factors: weather conditions, soil health, market trends, and crop-specific requirements.
            
            Format your response with:
            1. Executive Summary
            2. Detailed Analysis
            3. Specific Recommendations
            4. Risk Assessment
            5. Timeline for actions
            
            Be specific and actionable in your advice.
            """
        )
    
    def execute(self, state: AgentState) -> AgentState:
        """Execute main reasoning and tool orchestration"""
        logger.info("Main Agent executing...")
        
        tool_results = {}
        state.tool_calls = []
        
        # Execute tools based on plan
        if state.decomposed_query:
            for tool_name in state.decomposed_query.get("tools_needed", []):
                if tool_name in self.tools:
                    try:
                        # Call appropriate tool with context
                        if tool_name == "weather_api":
                            result = self.tools[tool_name]._run(
                                location=state.decomposed_query.get("location", "default")
                            )
                        elif tool_name == "crop_database":
                            result = self.tools[tool_name]._run(
                                crop_name="corn",  # Extract from query in production
                                query_type="general"
                            )
                        elif tool_name == "market_prices":
                            result = self.tools[tool_name]._run(
                                crop="corn",
                                region=state.decomposed_query.get("location", "national")
                            )
                        else:
                            result = self.tools[tool_name]._run()
                        
                        tool_results[tool_name] = result
                        state.tool_calls.append({
                            "tool": tool_name,
                            "input": state.decomposed_query.get("location", "default"),
                            "output": result,
                            "timestamp": datetime.now().isoformat()
                        })
                        
                    except Exception as e:
                        logger.error(f"Tool {tool_name} error: {e}")
                        state.errors = state.errors or []
                        state.errors.append(f"Tool {tool_name} error: {e}")
        
        # Generate main response
        prompt_text = self.prompt.format(
            user_query=state.user_query,
            plan=json.dumps(state.decomposed_query, indent=2),
            tool_results=json.dumps(tool_results, indent=2)
        )
        
        response = self.llm.invoke(prompt_text)
        state.main_agent_response = response.content
        
        # Calculate initial confidence score
        state.confidence_score = self.calculate_confidence(tool_results, state.errors or [])
        
        logger.info("Main Agent completed reasoning")
        return state
    
    def calculate_confidence(self, tool_results: Dict, errors: List) -> float:
        """Calculate confidence score based on tool availability and errors"""
        base_score = 0.8
        
        # Reduce score for each error
        error_penalty = len(errors) * 0.1
        
        # Increase score for successful tool calls
        tool_bonus = len(tool_results) * 0.05
        
        return max(0.0, min(1.0, base_score - error_penalty + tool_bonus))

class ErrorCheckerAgent:
    def __init__(self, llm, db_manager):
        self.llm = llm
        self.db_manager = db_manager
        
        self.verification_prompt = PromptTemplate(
            input_variables=["main_response", "tool_calls", "sources"],
            template="""
            You are an agricultural fact-checker and quality assurance expert.
            
            Main Agent Response: {main_response}
            Tool Calls Made: {tool_calls}
            Data Sources: {sources}
            
            Perform comprehensive verification:
            1. Check factual accuracy of agricultural advice
            2. Verify consistency between tool data and recommendations
            3. Identify potential contradictions or gaps
            4. Assess completeness of the response
            5. Check for potential harmful advice
            
            Return a JSON structure with:
            - "approved": boolean
            - "confidence_adjustment": float (-0.3 to +0.2)
            - "issues_found": list of issues
            - "corrections": list of suggested corrections
            - "missing_info": list of missing critical information
            """
        )
    
    def execute(self, state: AgentState) -> AgentState:
        """Verify and validate the main agent's response"""
        logger.info("Error Checker Agent executing...")
        
        # Compile sources from tool calls
        sources = []
        if state.tool_calls:
            for call in state.tool_calls:
                sources.append(f"{call['tool']}: {call['timestamp']}")
        
        state.sources = sources
        
        # Perform verification
        prompt_text = self.verification_prompt.format(
            main_response=state.main_agent_response,
            tool_calls=json.dumps(state.tool_calls, indent=2),
            sources=json.dumps(sources)
        )
        
        verification_response = self.llm.invoke(prompt_text)
        
        try:
            # Parse verification results (simplified for demo)
            verification_results = {
                "approved": True,
                "confidence_adjustment": 0.0,
                "issues_found": [],
                "corrections": [],
                "missing_info": []
            }
            
            state.error_check_results = verification_results
            
            # Adjust confidence score
            state.confidence_score += verification_results.get("confidence_adjustment", 0.0)
            state.confidence_score = max(0.0, min(1.0, state.confidence_score))
            
            # Generate final answer
            if verification_results["approved"]:
                state.final_answer = state.main_agent_response
                if verification_results["corrections"]:
                    state.final_answer += "\n\nAdditional Notes:\n" + "\n".join(verification_results["corrections"])
            else:
                state.final_answer = "I need to gather more information before providing agricultural advice. Please provide more specific details about your location and requirements."
            
            logger.info("Error checking completed successfully")
            
        except Exception as e:
            logger.error(f"Error checking failed: {e}")
            state.errors = state.errors or []
            state.errors.append(f"Verification error: {e}")
            state.final_answer = state.main_agent_response  # Fallback
        
        return state

# Main Agricultural Advisor System
class AgriculturalAdvisor:
    def __init__(self, gemini_api_key: str):
        """Initialize the three-agent agricultural advisor system"""
        
        # Initialize LLM
        os.environ["GOOGLE_API_KEY"] = gemini_api_key
        self.llm = ChatGoogleGenerativeAI(model="gemini-pro")
        
        # Initialize database manager
        self.db_manager = DatabaseManager()
        
        # Initialize tools
        self.tools = [
            WeatherTool(),
            CropDatabaseTool(self.db_manager),
            MarketPriceTool(),
            SoilAnalysisTool()
        ]
        
        # Initialize agents
        self.planner = PlannerAgent(self.llm)
        self.main_agent = MainAgent(self.llm, self.tools, self.db_manager)
        self.error_checker = ErrorCheckerAgent(self.llm, self.db_manager)
        
        # Build LangGraph workflow
        self.build_workflow()
    
    def build_workflow(self):
        """Build the LangGraph workflow"""
        
        def planner_node(state: AgentState) -> AgentState:
            return self.planner.execute(state)
        
        def main_agent_node(state: AgentState) -> AgentState:
            return self.main_agent.execute(state)
        
        def error_checker_node(state: AgentState) -> AgentState:
            return self.error_checker.execute(state)
        
        # Create workflow graph
        workflow = Graph()
        
        # Add nodes
        workflow.add_node("planner", planner_node)
        workflow.add_node("main_agent", main_agent_node)
        workflow.add_node("error_checker", error_checker_node)
        
        # Add edges
        workflow.add_edge("planner", "main_agent")
        workflow.add_edge("main_agent", "error_checker")
        workflow.add_edge("error_checker", END)
        
        # Set entry point
        workflow.set_entry_point("planner")
        
        # Compile the workflow
        self.app = workflow.compile()
    
    def process_query(self, user_query: str) -> Dict[str, Any]:
        """Process a user query through the three-agent system"""
        
        logger.info(f"Processing query: {user_query}")
        
        # Initialize state
        initial_state = AgentState(user_query=user_query)
        
        try:
            # Execute workflow
            final_state = self.app.invoke(initial_state)
            
            # Store interaction for metrics
            self.store_interaction(user_query, final_state)
            
            return {
                "answer": final_state.final_answer,
                "confidence": final_state.confidence_score,
                "sources": final_state.sources or [],
                "tool_calls": len(final_state.tool_calls or []),
                "processing_time": "calculated_in_production"
            }
            
        except Exception as e:
            logger.error(f"Workflow execution error: {e}")
            return {
                "answer": "I apologize, but I encountered an error processing your request. Please try again with a more specific question.",
                "confidence": 0.0,
                "sources": [],
                "error": str(e)
            }
    
    def store_interaction(self, query: str, final_state: AgentState):
        """Store interaction for metrics and learning"""
        try:
            cursor = self.db_manager.metrics_cursor
            cursor.execute(
                "INSERT INTO user_feedback (query, response, rating) VALUES (?, ?, ?)",
                (query, final_state.final_answer, None)  # Rating filled later by user feedback
            )
            self.db_manager.metrics_conn.commit()
        except Exception as e:
            logger.error(f"Failed to store interaction: {e}")

# Usage Example and Testing
def main():
    """Example usage of the Agricultural Advisor system"""
    
    # Initialize system (replace with your actual Gemini API key)
    advisor = AgriculturalAdvisor("your-gemini-api-key")
    
    # Example queries
    test_queries = [
        "What crops should I plant in Maharashtra during monsoon season?",
        "My corn plants are showing yellow leaves. What could be the problem?",
        "What are the current market prices for wheat in Punjab?",
        "Should I irrigate my tomato field given the weather forecast?"
    ]
    
    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"Query: {query}")
        print('='*60)
        
        result = advisor.process_query(query)
        
        print(f"Answer: {result['answer']}")
        print(f"Confidence: {result['confidence']:.2f}")
        print(f"Sources: {result['sources']}")
        print(f"Tool Calls: {result['tool_calls']}")

if __name__ == "__main__":
    main()
